# -*- coding: utf-8 -*-
# @author Marc Chakiachvili


import eHive



class OLSRunnable(eHive.BaseRunnable):
    """ OLS MySQL loader runnable class for eHive integration """

    def param_defaults(self):
        return {
            'db_version': 96,
        }

    def fetch_input(self):
        self.warning("Fetch the world !")
        print("alpha is", self.param_required('alpha'))
        print("beta is", self.param_required('beta'))

    def run(self):
        self.warning("Run the world !")
        s = self.param('alpha') + self.param('beta')
        print("set gamma to", s)
        self.param('gamma', s)

    def write_output(self):
        self.warning("Write to the world !")
        print("gamma is", self.param('gamma'))
        self.dataflow({'gamma': self.param('gamma')}, 2)
