import unittest
import GST
from GSTCommons import Std1Q_XYI as Std
import numpy as np

class WriteAndLoadTestCase(unittest.TestCase):

    def setUp(self):
        pass

    def assertArraysAlmostEqual(self,a,b):
        self.assertAlmostEqual( np.linalg.norm(a-b), 0 )


class TestWriteAndLoad(WriteAndLoadTestCase):

    def test_paramfile(self):
        d = {'a': 1, 'b': 2 }
        GST.Writers.write_parameter_file("temp_test_files/paramFile.json", d)
        d2 = GST.Loaders.load_parameter_file("temp_test_files/paramFile.json")
        self.assertEqual(d,d2)

    def test_dataset_file(self):

        strList = GST.gatestring_list( [(), ('Gx',), ('Gx','Gy') ] )
        weighted_strList = [ GST.WeightedGateString((), weight=0.1), 
                             GST.WeightedGateString(('Gx',), weight=2.0),
                             GST.WeightedGateString(('Gx','Gy'), weight=1.5) ]
        GST.write_empty_dataset_file("temp_test_files/emptyDataset.txt", strList, numZeroCols=2, appendWeightsColumn=False)
        GST.write_empty_dataset_file("temp_test_files/emptyDataset2.txt", weighted_strList, 
                                  headerString='## Columns = myplus count, myminus count', appendWeightsColumn=True)
        
        ds = GST.DataSet(spamLabels=['plus','minus'])
        ds.add_count_dict( ('Gx',), {'plus': 10, 'minus': 90} )
        ds.add_count_dict( ('Gx','Gy'), {'plus': 40, 'minus': 60} )
        ds.done_adding_data()

        GST.write_dataset_file("temp_test_files/dataset_loadwrite.txt", GST.gatestring_list(ds.keys()), ds)
        ds2 = GST.load_dataset("temp_test_files/dataset_loadwrite.txt")

        for s in ds:
            self.assertEqual(ds[s]['plus'],ds2[s]['plus'])
            self.assertEqual(ds[s]['minus'],ds2[s]['minus'])

    def test_gatestring_list_file(self):
        strList = GST.gatestring_list( [(), ('Gx',), ('Gx','Gy') ] )
        GST.write_gatestring_list("temp_test_files/gatestringlist_loadwrite.txt", strList, "My Header")
        strList2 = GST.load_gatestring_list("temp_test_files/gatestringlist_loadwrite.txt")
        self.assertEqual(strList, strList2)


    def test_gatestring_list_file(self):
        strList = GST.gatestring_list( [(), ('Gx',), ('Gx','Gy') ] )
        GST.write_gatestring_list("temp_test_files/gatestringlist_loadwrite.txt", strList, "My Header")
        strList2 = GST.load_gatestring_list("temp_test_files/gatestringlist_loadwrite.txt")
        self.assertEqual(strList, strList2)

        
    def test_gateset_file(self):
        GST.write_gateset(Std.gs_target, "temp_test_files/gateset_loadwrite.txt", "My title")
        gs = GST.load_gateset("temp_test_files/gateset_loadwrite.txt")
        self.assertAlmostEqual(gs.diff_frobenius(Std.gs_target), 0)

        gateset_txt = """# Gateset file using other allowed formats
rho0
StateVec
1 0

rho1
DensityMx
0 0
0 1

E
StateVec
0 1

Gi
UnitaryMx
 1 0
 0 1

Gx
UnitaryMxExp
 0    pi/4
pi/4   0

Gy
UnitaryMxExp
 0       -1j*pi/4
1j*pi/4    0

Gx2
UnitaryMx
 0  1
 1  0

Gy2
UnitaryMx
 0   -1j
1j    0


IDENTITYVEC sqrt(2) 0 0 0
SPAMLABEL plus0 = rho0 E
SPAMLABEL plus1 = rho1 E
SPAMLABEL minus = remainder
"""
        open("temp_test_files/formatExample.gateset","w").write(gateset_txt)
        gs_formats = GST.load_gateset("temp_test_files/formatExample.gateset")
        #print gs_formats

        rotXPi   = GST.build_gate( [2],[('Q0',)], "X(pi,Q0)").matrix
        rotYPi   = GST.build_gate( [2],[('Q0',)], "Y(pi,Q0)").matrix
        rotXPiOv2   = GST.build_gate( [2],[('Q0',)], "X(pi/2,Q0)").matrix        
        rotYPiOv2   = GST.build_gate( [2],[('Q0',)], "Y(pi/2,Q0)").matrix        

        self.assertArraysAlmostEqual(gs_formats['Gi'], np.identity(4,'d'))
        self.assertArraysAlmostEqual(gs_formats['Gx'], rotXPiOv2)
        self.assertArraysAlmostEqual(gs_formats['Gy'], rotYPiOv2)
        self.assertArraysAlmostEqual(gs_formats['Gx2'], rotXPi)
        self.assertArraysAlmostEqual(gs_formats['Gy2'], rotYPi)

        self.assertArraysAlmostEqual(gs_formats.rhoVecs[0], 1/np.sqrt(2)*np.array([[1],[0],[0],[-1]],'d'))
        self.assertArraysAlmostEqual(gs_formats.rhoVecs[1], 1/np.sqrt(2)*np.array([[1],[0],[0],[1]],'d'))
        self.assertArraysAlmostEqual(gs_formats.EVecs[0], 1/np.sqrt(2)*np.array([[1],[0],[0],[-1]],'d'))

        #GST.print_mx( rotXPi )
        #GST.print_mx( rotYPi )
        #GST.print_mx( rotXPiOv2 )
        #GST.print_mx( rotYPiOv2 )



        
    def test_gatestring_dict_file(self):
        file_txt = "# Gate string dictionary\nF1 GxGx\nF2 GxGy"  #TODO: make a Writers function for gate string dicts
        open("temp_test_files/gatestringdict_loadwrite.txt","w").write(file_txt)

        d = GST.load_gatestring_dict("temp_test_files/gatestringdict_loadwrite.txt")
        self.assertEqual( tuple(d['F1']), ('Gx','Gx'))

        


if __name__ == "__main__":
    unittest.main(verbosity=2)

    
