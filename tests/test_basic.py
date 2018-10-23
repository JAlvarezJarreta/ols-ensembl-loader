# -*- coding: utf-8 -*-
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
import logging
import unittest
import warnings

import ebi.ols.api.helpers as helpers
from bio.ensembl.ontology.loader import OlsLoader
from bio.ensembl.ontology.loader.db import *
from bio.ensembl.ontology.loader.models import *
from ebi.ols.api.client import OlsClient

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s \t: %(module)s(%(lineno)d) - \t%(message)s',
                    datefmt='%m-%d %H:%M:%S')

logger = logging.getLogger(__name__)

logging.getLogger('urllib3.connectionpool').setLevel(logging.WARNING)


def ignore_warnings(test_func):
    def do_test(self, *args, **kwargs):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Warning)
            test_func(self, *args, **kwargs)

    return do_test


class TestOLSLoader(unittest.TestCase):
    _multiprocess_shared_ = False
    db_url = 'sqlite://'

    def setUp(self):
        dal.wipe_schema(self.db_url)
        warnings.simplefilter("ignore", ResourceWarning)
        self.loader = OlsLoader(self.db_url, echo=False)
        self.client = OlsClient()

    @ignore_warnings
    def testLoadOntology(self):
        # test retrieve
        # test try to create duplicated
        ontology_name = 'cvdo'

        with dal.session_scope() as session:
            m_ontology = self.loader.load_ontology(ontology_name)
            session.add(m_ontology)
            logger.info('Loaded ontology %s', m_ontology)
            logger.info('number of Terms %s', m_ontology.number_of_terms)
            r_ontology = session.query(Ontology).filter_by(name=ontology_name,
                                                           namespace='cvdo').one()
            logger.info('(RE) Loaded ontology %s', r_ontology)
            self.assertEqual(m_ontology.name, r_ontology.name)
            self.assertEqual(m_ontology.version, r_ontology.version)
            assert isinstance(r_ontology, Ontology)
            # automatically create another one with another namespace
            new_ontology, created = get_one_or_create(Ontology,
                                                      session,
                                                      name=r_ontology.name,
                                                      namespace='another_namespace')

            self.assertTrue(created)
            for i in range(0, 5):
                session.add(Term(accession='CCC_00000{}'.format(i),
                                 name='Term {}'.format(i),
                                 ontology=r_ontology,
                                 is_root=False,
                                 is_obsolete=False))
            self.assertTrue(new_ontology.name == r_ontology.name)

        session = dal.get_session()
        self.assertEqual(5, session.query(Term).count())
        ontologies = session.query(Ontology).filter_by(name=ontology_name)
        self.assertEqual(ontologies.count(), 2)
        session = dal.get_session()
        self.loader.wipe_ontology(ontology_name=ontology_name)
        ontologies = session.query(Ontology).filter_by(name=ontology_name).count()
        self.assertEqual(ontologies, 0)

    @ignore_warnings
    def testLoadOntologyTerms(self):
        session = dal.get_session()
        ontology_name = 'cio'
        expected = self.loader.load_ontology_terms(ontology_name)
        logger.info('Expected terms %s', expected)
        s_terms = session.query(Term).filter(Ontology.name == ontology_name)
        inserted = s_terms.count()
        logger.info('Inserted terms %s', inserted)
        self.assertEqual(expected, inserted)

    @ignore_warnings
    def testLoadTimeMeta(self):
        ontology_name = 'bfo'
        self.loader.options['wipe'] = True
        with dal.session_scope() as session:
            o_ontology = self.client.ontology(ontology_name)
            m_ontology = self.loader.load_ontology(ontology_name)
            terms = self.loader.load_ontology_terms(ontology_name)
            session.add(m_ontology)
            self.assertIsInstance(m_ontology, Ontology)
        session = dal.get_session()
        meta_file_date = session.query(Meta).filter_by(meta_key=ontology_name + '_file_date').one()
        meta_start = session.query(Meta).filter_by(meta_key=ontology_name + '_load_date').one()
        self.assertTrue(
            datetime.datetime.strptime(meta_start.meta_value, ontology_name.upper() + "/%c") < datetime.datetime.now())
        logger.debug('meta load_all date: %s', meta_start)
        logger.debug('meta file date: %s', meta_file_date)
        try:
            datetime.datetime.strptime(meta_file_date.meta_value, ontology_name.upper() + "/%c")
            datetime.datetime.strptime(meta_start.meta_value, ontology_name.upper() + "/%c")
        except ValueError:
            self.fail('Wrong date format')

    @ignore_warnings
    def testCascadeDelete(self):
        with dal.session_scope() as session:
            m_ontology = Ontology(name='GO', _namespace='namespace', _version='1', title='Ontology test')
            m_ontology_2 = Ontology(name='GO', _namespace='namespace 2', _version='1', title='Ontology test 2')
            m_ontology_3 = Ontology(name='FPO', _namespace='namespace 3', _version='1', title='Ontology test 2')
            session.add(m_ontology)
            session.add(m_ontology_2)
            session.add(m_ontology_3)
            rel_type, created = get_one_or_create(RelationType,
                                                  session,
                                                  name='is_a')
            for i in range(1, 5):
                m_term = Term(accession='T:0000%s' % i, name='Term %s' % i, ontology=m_ontology)
                m_term_2 = Term(accession='T2:0000%s' % i, name='Term %s' % i, ontology=m_ontology_2)
                m_term_3 = Term(accession='T3:0000%s' % i, name='Term %s' % i, ontology=m_ontology_3)
                syn_1 = Synonym(name='TS:000%s' % i, type=SynonymTypeEnum.EXACT, db_xref='REF:000%s' % i)
                m_term.synonyms.append(syn_1)
                syn_2 = Synonym(name='TS2:000%s' % i, type=SynonymTypeEnum.EXACT, db_xref='REF:000%s' % i)
                m_term_2.synonyms.append(syn_2)
                session.add_all([syn_1, syn_2])
                alt_id = AltId(accession='ATL:000%s' % i)
                m_term.alt_ids.append(alt_id)
                session.add(alt_id)
                m_term.add_child_relation(session=session, rel_type=rel_type, child_term=m_term_3)
                m_term.add_parent_relation(session=session, rel_type=rel_type, parent_term=m_term_2)
                closure_1 = Closure(child_term=m_term, parent_term=m_term_2, distance=1, ontology=m_ontology)
                closure_2 = Closure(parent_term=m_term, child_term=m_term_3, distance=3, ontology=m_ontology_2)
                closure_3 = Closure(parent_term=m_term_2, child_term=m_term_3, subparent_term=m_term, distance=2,
                                    ontology=m_ontology_3)
                session.add_all([closure_1, closure_2, closure_3])

        with dal.session_scope() as session:
            self.loader.wipe_ontology('GO')
            self.assertEqual(session.query(Term).count(), 4)
            self.assertEqual(session.query(Synonym).count(), 0)
            self.assertEqual(session.query(AltId).count(), 0)
            self.assertEqual(session.query(Relation).count(), 0)
            self.assertEqual(session.query(Closure).count(), 0)

    @ignore_warnings
    def testMeta(self):
        session = dal.get_session()
        self.loader.init_meta()
        metas = session.query(Meta).all()
        self.assertGreaterEqual(len(metas), 2)

    @ignore_warnings
    def testEncodingTerm(self):
        self.loader.options['process_relations'] = False
        self.loader.options['process_parents'] = False
        session = dal.get_session()
        m_ontology = self.loader.load_ontology('fypo')
        session.add(m_ontology)
        term = helpers.Term(ontology_name='fypo', iri='http://purl.obolibrary.org/obo/FYPO_0005645')
        o_term = self.client.detail(term)
        m_term = self.loader.load_term(o_term, m_ontology, session)
        self.assertIn('λ', m_term.description)

    @ignore_warnings
    def testSingleTerm(self):
        self.loader.options['process_relations'] = True
        self.loader.options['process_parents'] = True

        with dal.session_scope() as session:
            m_ontology = self.loader.load_ontology('fypo')
            session.add(m_ontology)
            term = helpers.Term(ontology_name='fypo', iri='http://purl.obolibrary.org/obo/FYPO_0000257')
            o_term = self.client.detail(term)
            m_term = self.loader.load_term(o_term, m_ontology, session)
            session.commit()
            self.assertGreaterEqual(len(m_term.child_terms), 4)

    @ignore_warnings
    def testOntologiesList(self):
        self.assertIsInstance(self.loader.allowed_ontologies, list)
        self.assertIn('go', self.loader.allowed_ontologies)

    @ignore_warnings
    def testRelationsShips(self):
        with dal.session_scope() as session:
            m_ontology = self.loader.load_ontology('bto')
            session.add(m_ontology)
            list_go = ['GO_0019953', 'GO_0019954', 'GO_0022414', 'GO_0032504', 'GO_0032505', 'GO_0061887',
                       'GO_0000228', 'GO_0000003', 'GO_0031981', 'GO_0000176', 'GO_0000228', 'GO_0005654',
                       'GO_0005730', 'GO_0031595', 'GO_0034399', 'GO_0097356', 'GO_1990934', 'GO_2000241',
                       'GO_2000242', 'GO_2000243']
            list_bto = ['BTO_000000%s' % i for i in range(0, 10)]

            for s_term in list_bto:
                term = helpers.Term(ontology_name='bto', iri='http://purl.obolibrary.org/obo/' + s_term)
                o_term = self.client.detail(term)
                m_term = self.loader.load_term(o_term, m_ontology, session)
                session.add(m_term)
                self.assertGreaterEqual(len(m_term.parent_terms), 0)

    @ignore_warnings
    def testRelationOtherOntology(self):
        self.loader.options['process_relations'] = True
        self.loader.options['process_parents'] = True
        with dal.session_scope() as session:
            m_ontology = self.loader.load_ontology('efo')
            session.add(m_ontology)
            term = helpers.Term(ontology_name='efo', iri='http://www.ebi.ac.uk/efo/EFO_0002215')
            o_term = self.client.detail(term)
            m_term = self.loader.load_term(o_term, m_ontology, session)
            session.add(m_term)
            self.assertGreaterEqual(session.query(Ontology).count(), 2)
            term = session.query(Term).filter_by(accession='BTO:0000164')
            self.assertEqual(1, term.count())

    @ignore_warnings
    def testSubsets(self):
        self.loader.options['process_relations'] = False
        self.loader.options['process_parents'] = False

        with dal.session_scope() as session:
            term = helpers.Term(ontology_name='go', iri='http://purl.obolibrary.org/obo/GO_0099565')
            o_term = self.client.detail(term)
            m_term = self.loader.load_term(o_term, 'go', session)
            session.add(m_term)
            subsets = session.query(Subset).all()
            for subset in subsets:
                self.assertIsNotNone(subset.definition)

            subset = helpers.Property(ontology_name='go',
                                      iri='http://www.geneontology.org/formats/oboInOwl#hasBroadSynonym')
            details = self.client.detail(subset)
            self.assertIsNone(details.definition, '')

    @ignore_warnings
    def testAltIds(self):
        self.loader.options['process_relations'] = False
        self.loader.options['process_parents'] = False

        with dal.session_scope() as session:
            o_term = self.client.term(identifier='http://purl.obolibrary.org/obo/GO_0005261', unique=True, silent=True)
            m_term = self.loader.load_term(o_term, 'go', session)
            session.add(m_term)
            self.assertGreaterEqual(len(m_term.alt_ids), 2)

    @ignore_warnings
    def testTrickTerm(self):
        self.loader.options['process_relations'] = True
        self.loader.options['process_parents'] = True

        with dal.session_scope() as session:
            # o_term = helpers.Term(ontology_name='fypo', iri='http://purl.obolibrary.org/obo/FYPO_0001330')
            o_term = self.client.term(identifier='http://purl.obolibrary.org/obo/FYPO_0001330', unique=True,
                                      silent=True)
            m_term = self.loader.load_term(o_term, 'fypo', session)
            session.add(m_term)
            found = False
            for child in m_term.child_terms:
                found = found or (child.parent_term.accession == 'CHEBI:24431')
        self.assertTrue(found)

    @ignore_warnings
    def testLoadSubsetLongDef(self):
        self.loader.options['process_relations'] = False
        # https://www.ebi.ac.uk/ols/api/ontologies/mondo/properties/http%253A%252F%252Fpurl.obolibrary.org%252Fobo%252Fmondo%2523prototype_pattern
        with dal.session_scope() as session:
            h_property = helpers.Property(ontology_name='mondo',
                                          iri='http://purl.obolibrary.org/obo/mondo#prototype_pattern')
            o_property = self.client.detail(h_property)
            m_subset = self.loader.load_subset(subset_name=o_property.short_form,
                                               ontology_name='mondo',
                                               session=session)
            self.assertGreaterEqual(len(m_subset.definition), 128)

    @ignore_warnings
    def testLoopingRelations(self):
        session = dal.get_session()
        ontology_name = 'mondo'
        expected = self.loader.load_ontology_terms(ontology_name, start=0, end=100)
        logger.info('Expected terms %s', expected)
        s_terms = session.query(Term).filter(Ontology.name == ontology_name)
        inserted = s_terms.count()
        logger.info('Inserted terms %s', inserted)
        self.assertGreaterEqual(inserted, expected)

    @ignore_warnings
    def testRelatedNonExpected(self):
        with dal.session_scope() as session:
            ontology_name = 'eco'
            expected = self.loader.load_ontology_terms(ontology_name, start=0, end=50)
            logger.info('Expected terms %s', expected)
            s_terms = session.query(Term).filter(Ontology.name == ontology_name)
            inserted = s_terms.count()
            logger.info('Inserted terms %s', inserted)
            self.assertGreaterEqual(inserted, expected)

    @ignore_warnings
    def testRelationSingleTerm(self):
        with dal.session_scope() as session:
            o_term = self.client.term(identifier='http://purl.obolibrary.org/obo/ECO_0007571', unique=True, silent=True)
            m_term = self.loader.load_term(o_term, 'eco', session)
            session.add(m_term)
            session.commit()

    def testMissingSubset(self):
        with dal.session_scope() as session:
            subset = self.loader.load_subset('efo_slim', 'efo', session)
            self.assertEqual(subset.definition, 'Efo slim')
