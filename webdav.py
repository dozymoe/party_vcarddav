#This file is part of Tryton.  The COPYRIGHT file at the top level of
#this repository contains the full copyright notices and license terms.
from trytond.model import ModelView, ModelSQL
from DAV.errors import DAV_NotFound, DAV_Forbidden
import base64
import urlparse

CARDDAV_NS = 'urn:ietf:params:xml:ns:carddav'


class Collection(ModelSQL, ModelView):

    _name = "webdav.collection"

    def vcard(self, cursor, user, uri, context=None):
        '''
        Return party ids of the vcard in uri or False

        :param cursor: the database cursor
        :param user: the user id
        :param uri: the uri
        :param context: the context
        :return: party id
            or None if there is no party
            or False if not in Contacts
        '''
        party_obj = self.pool.get('party.party')

        if uri and uri.startswith('Contacts/'):
            uuid = uri[9:-4]
            party_ids = party_obj.search(cursor, user, [
                ('uuid', '=', uuid),
                ], limit=1, context=context)
            if party_ids:
                return party_ids[0]
            return None
        if uri == 'Contacts':
            return None
        return False

    def _carddav_filter_domain(self, cursor, user, filter, context=None):
        '''
        Return a domain for the carddav filter

        :param cursor: the database cursor
        :param user: the user id
        :param filter: the DOM Element of filter
        :param context: the context

        :return: a list for domain
        '''
        address_obj = self.pool.get('party.address')
        contact_mechanism_obj = self.pool.get('party.contact_mechanism')

        res = []
        if not filter:
            return []
        if filter.localName == 'addressbook-query':
            addressbook_filter = filter.getElementsByTagNameNS(
                    'urn:ietf:params:xml:ns:carddav', 'filter')[0]
            if addressbook_filter.hasAttribute('test') \
                    and addressbook_filter.getAttribute('test') == 'allof':
                res.append('AND')
            else:
                res.append('OR')

            for prop in addressbook_filter.childNodes:
                name = prop.getAttribute('name').lower()
                field = None
                if name == 'fn':
                    field = 'rec_name'
                if name == 'n':
                    field = 'name'
                if name == 'uid':
                    field = 'uid'
                if name == 'adr':
                    field = 'rec_name'
                if name in ('mail', 'tel'):
                    field = 'value'
                if field:
                    res2 = []
                    if prop.hasAttribute('test') \
                            and prop.addressbook_filter.getAttribute('test') == 'allof':
                        res2.append('AND')
                    else:
                        res2.append('OR')
                    if prop.getElementsByTagNameNS(CARDDAV_NS, 'is-not-defined'):
                        res2.append((field, '=', False))
                    for text_match in prop.getElementsByTagNameNS(CARDDAV_NS,
                            'text-match'):
                        value = text_match.firstChild.data
                        negate = False
                        if text_match.hasAttribute('negate-condition') \
                                and text_match.getAttribute(
                                        'negate-condition') == 'yes':
                            negate = True
                        type = 'contains'
                        if text_match.hasAttribute('match-type'):
                            type = text_match.getAttribute('match-type')
                        if type == 'equals':
                            pass
                        elif type in ('contains', 'substring'):
                            value = '%' + value + '%'
                        elif type == 'starts-with':
                            value = value + '%'
                        elif type == 'ends-with':
                            value = '%' + value
                        if not negate:
                            res2.append((field, 'ilike', value))
                        else:
                            res2.append((field, 'not ilike', value))
                    if name == 'adr':
                        domain = res2
                        address_ids = address_obj.search(cursor, user, domain,
                                context=context)
                        res = [('addresses', 'in', address_ids)]
                    elif name in ('mail', 'tel'):
                        if name == 'mail':
                            type = ['email']
                        else:
                            type = ['phone', 'mobile']
                        domain = [('type', 'in', type), res2]
                        contact_mechanism_ids = contact_mechanism_obj.search(cursor,
                                user, domain, context=context)
                        res2 = [('contact_mechanisms', 'in', contact_mechanism_ids)]
                    res.append(res2)
        return res

    def get_childs(self, cursor, user, uri, filter=None, context=None,
            cache=None):
        party_obj = self.pool.get('party.party')

        if uri in ('Contacts', 'Contacts/'):
            domain = self._carddav_filter_domain(cursor, user, filter,
                    context=context)
            party_ids = party_obj.search(cursor, user, domain, context=context)
            parties = party_obj.browse(cursor, user, party_ids, context=context)
            return [x.uuid + '.vcf' for x in parties]
        party_id = self.vcard(cursor, user, uri, context=context)
        if party_id or party_id is None:
            return []
        res = super(Collection, self).get_childs(cursor, user, uri,
                filter=filter, context=context, cache=cache)
        if not uri and not filter:
            res.append('Contacts')
        return res

    def get_resourcetype(self, cursor, user, uri, context=None, cache=None):
        from DAV.constants import COLLECTION, OBJECT
        party_id = self.vcard(cursor, user, uri, context=context)
        if party_id:
            return OBJECT
        elif party_id is None:
            return COLLECTION
        return super(Collection, self).get_resourcetype(cursor, user, uri,
                context=context, cache=cache)

    def get_contenttype(self, cursor, user, uri, context=None, cache=None):
        if self.vcard(cursor, user, uri, context=context):
            return 'text/x-vcard'
        return super(Collection, self).get_contenttype(cursor, user, uri,
                context=context, cache=cache)

    def get_creationdate(self, cursor, user, uri, context=None, cache=None):
        party_obj = self.pool.get('party.party')
        party_id = self.vcard(cursor, user, uri, context=context)
        if party_id is None:
            raise DAV_NotFound
        if party_id:
            cursor.execute('SELECT EXTRACT(epoch FROM create_date) ' \
                    'FROM "' + party_obj._table + '" ' \
                        'WHERE id = %s', (party_id,))
            fetchone = cursor.fetchone()
            if fetchone:
                return fetchone[0]
        return super(Collection, self).get_creationdate(cursor, user, uri,
                context=context, cache=cache)

    def get_lastmodified(self, cursor, user, uri, context=None, cache=None):
        party_obj = self.pool.get('party.party')
        address_obj = self.pool.get('party.address')
        contact_mechanism_obj = self.pool.get('party.contact_mechanism')

        party_id = self.vcard(cursor, user, uri, context=context)
        if party_id:
            cursor.execute('SELECT ' \
                    'MAX(EXTRACT(epoch FROM COALESCE(p.write_date, p.create_date))), ' \
                    'MAX(EXTRACT(epoch FROM COALESCE(a.write_date, a.create_date))), ' \
                    'MAX(EXTRACT(epoch FROM COALESCE(c.write_date, c.create_date))) ' \
                    'FROM "' + party_obj._table + '" p ' \
                        'LEFT JOIN "' + address_obj._table + '" a ' \
                        'ON p.id = a.party ' \
                        'LEFT JOIN "' + contact_mechanism_obj._table + '" c ' \
                        'ON p.id = c.party ' \
                    'WHERE p.id = %s', (party_id,))
            return max(cursor.fetchone())
        return super(Collection, self).get_lastmodified(cursor, user, uri,
                context=context, cache=cache)

    def get_data(self, cursor, user, uri, context=None, cache=None):
        vcard_obj = self.pool.get('party_vcarddav.party.vcard', type='report')
        party_id = self.vcard(cursor, user, uri, context=context)
        if party_id is None:
            raise DAV_NotFound
        if party_id:
            val = vcard_obj.execute(cursor, user, [party_id],
                    {'id': party_id, 'ids': [party_id]},
                    context=context)
            return base64.decodestring(val[1])
        return super(Collection, self).get_data(cursor, user, uri,
                context=context, cache=cache)

    def get_address_data(self, cursor, user, uri, context=None, cache=None):
        vcard_obj = self.pool.get('party_vcarddav.party.vcard', type='report')
        party_id = self.vcard(cursor, user, uri, context=context)
        if not party_id:
            raise DAV_NotFound
        val = vcard_obj.execute(cursor, user, [party_id],
                {'id': party_id, 'ids': [party_id]},
                context=context)
        res = base64.decodestring(val[1])
        return res.decode('utf-8')

    def put(self, cursor, user, uri, data, content_type, context=None,
            cache=None):
        import vobject
        party_obj = self.pool.get('party.party')

        party_id = self.vcard(cursor, user, uri, context=context)
        if party_id is None:
            vcard = vobject.readOne(data)
            values = party_obj.vcard2values(cursor, user, None, vcard,
                    context=context)
            try:
                party_id = party_obj.create(cursor, user, values,
                        context=context)
            except:
                raise DAV_Forbidden
            party = party_obj.browse(cursor, user, party_id, context=context)
            return cursor.database_name + '/Contacts/' + party.uuid + '.vcf'
        if party_id:
            vcard = vobject.readOne(data)
            values = party_obj.vcard2values(cursor, user, party_id, vcard,
                    context=context)
            try:
                party_obj.write(cursor, user, party_id, values, context=context)
            except:
                raise DAV_Forbidden
            return
        return super(Collection, self).put(cursor, user, uri, data,
                content_type, context=context)

    def mkcol(self, cursor, user, uri, context=None, cache=None):
        party_id = self.vcard(cursor, user, uri, context=context)
        if party_id is None:
            raise DAV_Forbidden
        if party_id:
            raise DAV_Forbidden
        return super(Collection, self).mkcol(cursor, user, uri, context=context,
                cache=cache)

    def rmcol(self, cursor, user, uri, context=None, cache=None):
        party_id = self.vcard(cursor, user, uri, context=context)
        if party_id is None:
            raise DAV_Forbidden
        if party_id:
            raise DAV_Forbidden
        return super(Collection, self).rmcol(cursor, user, uri, context=context,
                cache=cache)

    def rm(self, cursor, user, uri, context=None, cache=None):
        party_obj = self.pool.get('party.party')

        party_id = self.vcard(cursor, user, uri, context=context)
        if party_id is None:
            raise DAV_Forbidden
        if party_id:
            try:
                party_obj.delete(cursor, user, party_id, context=context)
            except:
                raise DAV_Forbidden
            return 200
        return super(Collection, self).rm(cursor, user, uri, context=context,
                cache=cache)

    def exists(self, cursor, user, uri, context=None, cache=None):
        party_id = self.vcard(cursor, user, uri, context=context)
        if party_id is None or party_id:
            return 1
        return super(Collection, self).exists(cursor, user, uri, context=context,
                cache=cache)

Collection()
