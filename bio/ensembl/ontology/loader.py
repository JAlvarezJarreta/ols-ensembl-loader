#  -*- coding: utf-8 -*-
"""
.. See the NOTICE file distributed with this work for additional information
   regarding copyright ownership.
   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at
       http://www.apache.org/licenses/LICENSE-2.0
   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
"""
import datetime
import time
from os import getenv

import dateutil.parser
from coreapi.exceptions import NetworkError
from requests.exceptions import ConnectionError

import ebi.ols.api.helpers as helpers
from bio.ensembl.ontology.db import *
from bio.ensembl.ontology.models import *
from ebi.ols.api.client import OlsClient

logger = logging.getLogger(__name__)


class OlsLoader(object):
    """ class loader for mapping retrieved DTO from OLS client into expected database fields """
    __relation_map = {
        'parents': 'is_a',
        'children': 'is_a'
    }
    __ignored_relations = [
        'graph', 'jstree', 'descendants', 'ancestors', 'hierarchicalParents',  # 'parents',
        'hierarchicalAncestors', 'hierarchicalChildren', 'hierarchicalDescendants'
    ]

    __synonym_map = {
        'hasExactSynonym': 'EXACT',
        'hasBroadSynonym': 'BROAD',
        'hasNarrowSynonym': 'NARROW',
        'hasRelatedSynonym': 'RELATED'
    }
    _default_options = dict(
        echo=False,
        wipe=False,
        db_version=getenv('ENS_VERSION'),
        max_retry=5,
        timeout=720
    )

    def __init__(self, url, **options):
        self.db_url = url
        self.options = self._default_options
        self.options.update(options)
        self.client = OlsClient()
        self.retry = 0
        self.db_init = False
        dal.db_init(self.db_url, **self.options)

    def init_meta(self):
        with dal.session_scope() as session:
            metas = {
                'schema_version': self.options.get('db_version'),
                'schema_type': 'ontology'
            }
            for meta_key, meta_value in metas.items():
                get_one_or_create(Meta,
                                  session,
                                  meta_key=meta_key,
                                  create_method_kwargs=dict(meta_value=meta_value))
        self.db_init = True

    @property
    def allowed_ontologies(self):
        return ['go', 'so', 'pato', 'hp', 'vt', 'efo', 'po', 'eo', 'to', 'chebi', 'pr', 'fypo', 'peco', 'bfo', 'bto',
                'cl', 'cmo', 'eco', 'mod', 'mp', 'ogms', 'uo']

    def load_all(self, ontology_name):
        self.wipe_ontology(ontology_name)
        with dal.session_scope() as session:
            if self.options.get('wipe', False):
                logger.info('Removing ontology %s', ontology_name)
                metas = session.query(Meta).filter(Meta.meta_key.like("%" + ontology_name + "%")).all()
                for meta in metas:
                    meta.delete()
            else:
                logger.info('Updating ontology %s', ontology_name)
        with dal.session_scope() as session:
            start = datetime.datetime.now()
            meta, created = get_one_or_create(Meta,
                                              session,
                                              meta_key=ontology_name + '_load_date',
                                              create_method_kwargs=dict(
                                                  meta_value=ontology_name.upper() + '/' + start.strftime('%c')))
            if not created:
                meta.meta_value = start.strftime('%c')
            m_ontology = self.load_ontology(ontology_name, namespace=ontology_name)
            session.add(m_ontology)
            nb_terms = self.load_ontology_terms(m_ontology)
            ended = datetime.datetime.now()
            meta_time, created = get_one_or_create(Meta,
                                                   session,
                                                   meta_key=ontology_name + '_load_time',
                                                   create_method_kwargs=dict(
                                                       meta_value=(ended - start).total_seconds()))
            if not created:
                logger.debug('Updating meta_load_time to %s', (ended - start).total_seconds())
                meta_time.meta_value = (ended - start).total_seconds()
            return created

    def __call_client(self, method, *args, **kwargs):
        """
        Try 'max_retry' time to contact OLS api via its client, reraise error when limit reached.
        :param method: client method to call
        :param args:  client methods args
        :return: client response
        """
        retry = 0
        max_retry = self.options.get('max_retry')
        while retry < max_retry:
            try:
                logger.debug('Calling client.%s(%s)(%s)', method, args, kwargs)
                return self.client.__getattribute__(method)(*args, **kwargs)
            except (ConnectionError, NetworkError) as e:
                logger.error('Network error %s for %s(%s)(%s)', e, method, args, kwargs)
                # wait 5 seconds until next OLS api client try
                time.sleep(5)
                retry += 1
                if retry == max_retry:
                    logger.fatal('Max API retry for %s(%s)(%s)', method, args, kwargs)
                    raise e

    def load_ontology(self, ontology_name, namespace=None):
        with dal.session_scope() as session:
            o_ontology = self.__call_client('ontology', ontology_name)
            meta, created = get_one_or_create(Meta,
                                              session,
                                              meta_key=ontology_name + '_file_date',
                                              create_method_kwargs=dict(
                                                  meta_value=ontology_name.upper() + '/' + dateutil.parser.parse(
                                                      o_ontology.updated).strftime('%c')))

            if not created:
                meta.meta_value = dateutil.parser.parse(o_ontology.updated).strftime('%c')
            m_ontology, created = get_one_or_create(Ontology,
                                                    session,
                                                    name=o_ontology.ontology_id,
                                                    namespace=namespace or o_ontology.namespace,
                                                    create_method_kwargs={'helper': o_ontology})
            logger.info('Loaded ontology %s', m_ontology)
            return m_ontology
        return False

    @staticmethod
    def wipe_ontology(ontology_name):
        with dal.session_scope() as session:
            try:
                logger.debug('Delete ontology %s', ontology_name)
                ontologies = session.query(Ontology).filter_by(name=ontology_name).all()
                for ontology in ontologies:
                    logger.debug('Deleting ontology %s', ontology)
                    session.delete(ontology)
                return True
            except NoResultFound:
                logger.debug('Ontology not found')

    def load_ontology_terms(self, ontology):
        nb_terms = 0
        with dal.session_scope() as session:
            if type(ontology) is str:
                m_ontology = self.load_ontology(ontology)
                session.add(m_ontology)
                terms = self.__call_client('ontology', ontology).terms()
            elif isinstance(ontology, Ontology):
                m_ontology = ontology
                # session.add(m_ontology)
                terms = self.__call_client('ontology', ontology.name).terms()
            elif isinstance(ontology, helpers.Ontology):
                m_ontology = Ontology(helper=ontology)
                terms = ontology.terms()
            else:
                raise RuntimeError('Wrong parameter')
            logger.info('Loading %s terms for %s', len(terms), m_ontology.name)
            for o_term in terms:
                if o_term.is_defining_ontology and o_term.obo_id:
                    logger.debug('Loaded term (from OLS) %s', o_term)
                    logger.debug('Adding/Retrieving namespaced ontology %s', o_term.obo_name_space)
                    ontology, created = get_one_or_create(Ontology,
                                                          session,
                                                          name=m_ontology.name,
                                                          namespace=o_term.obo_name_space or m_ontology.name,
                                                          create_method_kwargs=dict(
                                                              version=m_ontology.version,
                                                              title=m_ontology.title))
                    term = self.load_term(o_term, ontology, session)
                    session.add(term)
                    nb_terms += 1
            return nb_terms
        return False

    def load_term(self, o_term, ontology, session):
        if type(ontology) is str:
            m_ontology = self.load_ontology(ontology)
        elif isinstance(ontology, Ontology):
            m_ontology = ontology
        elif isinstance(ontology, helpers.Ontology):
            m_ontology = Ontology(helper=ontology)
        else:
            raise RuntimeError('Wrong parameter')
        m_term, created = get_one_or_create(Term,
                                            session,
                                            accession=o_term.obo_id,
                                            create_method_kwargs=dict(helper=o_term,
                                                                      ontology=m_ontology))

        logger.info('Loaded Term %s ...', m_term)
        relation_types = [rel for rel in o_term.relations_types if rel not in self.__ignored_relations]
        logger.debug('Expected relations to load %s', relation_types)
        self.load_term_relations(m_term, relation_types, session)
        self.load_term_synonyms(m_term, o_term, session)
        self.load_alt_ids(o_term, m_term, session)
        self.load_term_subsets(m_term, session)
        logger.info('... Done')
        return m_term

    def load_alt_ids(self, o_term, m_term, session):
        session.query(AltId).filter(AltId.term == m_term).delete()
        for alt_id in o_term.annotation.has_alternative_id:
            logger.info('Loaded AltId %s', alt_id)
            m_term.alt_ids.append(AltId(accession=alt_id))
        return m_term

    def load_term_subsets(self, term, session):
        subsets = []
        if term.subsets:
            logger.info('   Loading term subsets: %s', term.subsets)
            subset_names = term.subsets.split(',')
            for subset_name in subset_names:
                logger.debug('      Processing subset %s', subset_name)
                search = self.__call_client('search', query=subset_name, filters={'ontology': term.ontology.name,
                                                                                  'type': 'property'})
                if len(search) == 1:
                    details = self.__call_client('detail', search[0])
                    subset, created = get_one_or_create(Subset, session,
                                                        name=subset_name,
                                                        definition=details.definition or '')
                    subsets.append(subset)
            logger.info('   ... Done')
        return subsets

    def load_term_relations(self, m_term, relation_types, session):
        # remove previous relationships
        session.query(Relation).filter(Relation.child_term == m_term).delete()
        session.query(Relation).filter(Relation.parent_term == m_term).delete()

        n_relations = 0
        for rel_name in relation_types:
            # updates relation types
            o_term = helpers.Term(ontology_name=m_term.ontology.name, iri=m_term.iri)
            o_relatives = o_term.load_relation(rel_name)
            relation_type, created = get_one_or_create(RelationType,
                                                       session,
                                                       name=self.__relation_map.get(rel_name, rel_name))

            logger.info('   Loading %s relation %s (%s)...', m_term.accession, rel_name, relation_type.name)
            logger.info('   %s related terms ', len(o_relatives))
            for o_related in o_relatives:
                if o_related.accession is not None and o_related.ontology_name in self.allowed_ontologies:

                    logger.debug('  Term is defined in another ontology: %s', o_related.ontology_name)
                    o_term_details = self.client.term(o_related.iri,
                                                        unique=True) if not o_related.is_defining_ontology else o_related
                    o_onto_details = self.__call_client('ontology', o_term_details.ontology_name)

                    r_ontology, created = get_one_or_create(Ontology,
                                                            session,
                                                            name=o_onto_details.ontology_id,
                                                            namespace=o_related.obo_name_space,
                                                            create_method_kwargs=dict(
                                                                version=o_onto_details.version,
                                                                title=o_onto_details.title))
                    m_related, created = get_one_or_create(Term,
                                                           session,
                                                           accession=o_term_details.obo_id,
                                                           create_method_kwargs=dict(
                                                               helper=o_term_details,
                                                               ontology=r_ontology,
                                                           ))
                    # hack to reverse OBO loading behavior
                    if rel_name == 'children':
                        parent_term = m_term
                        child_term = m_related
                    else:
                        parent_term = m_related
                        child_term = m_term
                    relation, r_created = get_one_or_create(Relation,
                                                            session,
                                                            parent_term=parent_term,
                                                            child_term=child_term,
                                                            relation_type=relation_type,
                                                            ontology=m_term.ontology)
                    n_relations += 1 if r_created else 0
                    logger.info('Loaded relation %s %s %s', m_term.accession, relation_type.name,
                                m_related.accession)
                else:
                    logger.info('Ignored related %s', o_related)
            logger.info('   ... Done (%s)', n_relations)
        return n_relations

    def load_term_synonyms(self, m_term, o_term, session=None):
        session = session or dal.get_session()
        logger.info('   Loading term synonyms...')
        session.query(Synonym).filter(Synonym.term == m_term).delete()
        n_synonyms = 0

        obo_synonyms = o_term.obo_synonym or []
        for synonym in obo_synonyms:
            if isinstance(synonym, dict):
                logger.info('   Term obo synonym %s - %s', synonym['name'], self.__synonym_map[synonym['scope']])
                db_xref = synonym['xrefs'][0]['database'] + ':' + synonym['xrefs'][0]['id'] \
                    if 'xrefs' in synonym and len(synonym['xrefs']) > 0 else ''
                m_syno, created = get_one_or_create(Synonym,
                                                    session,
                                                    term=m_term,
                                                    name=synonym['name'],
                                                    create_method_kwargs=dict(
                                                        db_xref=db_xref,
                                                        type=self.__synonym_map[synonym['scope']]))
                n_synonyms += 1 if created else 0
        # OBO Xref are winning against standard synonymz
        synonyms = o_term.synonyms or []
        for synonym in synonyms:
            logger.info('   Term synonym %s - EXACT - No dbXref', synonym)
            m_syno, created = get_one_or_create(Synonym,
                                                session,
                                                term=m_term, name=synonym, type='EXACT')
            n_synonyms += 1 if created else 0

        logger.info('   ... Done')
        return n_synonyms
