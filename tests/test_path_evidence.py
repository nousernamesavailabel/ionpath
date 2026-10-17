import unittest
import numpy as np
import pandas as pd
from path_evidence import select_path
from ft8_archive import distances

A=(61.5,-149)
B=(32.5,-97)


def fixture():
    return pd.DataFrame([
        dict(tx_lat=A[0],tx_lon=A[1],rx_lat=B[0],rx_lon=B[1],tx_sign='AK',rx_sign='TX',power=23,snr=-10),
        dict(tx_lat=B[0],tx_lon=B[1],rx_lat=A[0],rx_lon=A[1],tx_sign='TX',rx_sign='AK',power=37,snr=-21),
        dict(tx_lat=0,tx_lon=0,rx_lat=A[0],rx_lon=A[1],tx_sign='ELSE',rx_sign='AK',power=30,snr=-5),
        dict(tx_lat=np.nan,tx_lon=np.nan,rx_lat=B[0],rx_lon=B[1],tx_sign='UNKNOWN',rx_sign='TX',power=30,snr=-5),
    ])


class PathEvidence(unittest.TestCase):
    def test_reverse_receptions_keep_actual_power_snr_and_roles(self):
        chosen=select_path(fixture(),A,50,B,600,distances)
        self.assertEqual(chosen.path_direction.tolist(),['A → B','B → A'])
        reverse=chosen.iloc[1]
        self.assertEqual((reverse.a_sign,reverse.b_sign),('AK','TX'))
        self.assertEqual((reverse.tx_sign,reverse.rx_sign,reverse.power,reverse.snr),('TX','AK',37,-21))
        self.assertEqual((reverse.b_lat,reverse.b_lon),B)

    def test_callsign_applies_to_A_in_either_role_and_direction_filter_works(self):
        chosen=select_path(fixture(),A,50,B,600,distances,callsign=' ak ')
        self.assertEqual(len(chosen),2)
        self.assertEqual(len(select_path(fixture(),A,50,B,600,distances,callsign='TX')),0)
        for direction in ['A → B','B → A']:
            one=select_path(fixture(),A,50,B,600,distances,direction)
            self.assertEqual(one.path_direction.tolist(),[direction])
        self.assertTrue(select_path(fixture().iloc[1:2],A,50,B,600,distances).path_direction.eq('B → A').all())

    def test_overlap_does_not_duplicate_an_observation(self):
        chosen=select_path(fixture().iloc[:1],A,5000,A,5000,distances)
        self.assertEqual(len(chosen),1)
        self.assertEqual(chosen.path_direction.iloc[0],'A → B')


if __name__=='__main__':
    unittest.main()
