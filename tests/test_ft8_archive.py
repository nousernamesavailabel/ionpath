from datetime import datetime,timezone
from pathlib import Path
import tempfile
import unittest

import h5py
import numpy as np
from ft8_archive import read_archive

DTYPE=np.dtype([(name,'S4') for name in ['year','month','day','hour','min','sec']]+[
 ('ut1_unix','i8'),('call_sign_tx','S14'),('txlat','f8'),('txlon','f8'),
 ('call_sign_rx','S14'),('rxlat','f8'),('rxlon','f8'),('tfreq','f8'),('sn','f8'),('smode','S10'),('ssrc','S3')])


def write_fixture(path):
    records=np.zeros(6,dtype=DTYPE)
    for i,row in enumerate(records):
        for name,value in [('year','2024'),('month','05'),('day','12'),('hour',str(i)),('min','00'),('sec','00')]:
            row[name]=value.encode()
        row['ut1_unix']=int(datetime(2024,5,12,i,tzinfo=timezone.utc).timestamp())+18000
        row['call_sign_tx']=b'TX1' if i%2 else b'TX2'
        row['call_sign_rx']=b'RX1'
        row['txlat'],row['txlon']=61.5,-149
        row['rxlat'],row['rxlon']=32.5,-97
        row['tfreq'],row['sn']=14074500,-15
        row['smode'],row['ssrc']=b'FT8',b'PSK'
    records[1]['smode']=b'WSPR'
    records[2]['txlat']=0
    records[3]['rxlat']=0
    with h5py.File(path,'w') as file:
        file.create_dataset('Data/Table Layout',data=records)


class ArchiveReader(unittest.TestCase):
    def test_chunked_filters_newest_limit_and_timestamp_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'day.hdf5'
            write_fixture(path)
            frame,stats=read_archive(path,(61.5,-149),50,(32.5,-97),600,limit=2,chunk_size=2)
            self.assertEqual(stats['scanned_rows'],6)
            self.assertEqual(stats['ft8_rows'],5)
            self.assertEqual(stats['matching_rows'],3)
            self.assertTrue(stats['limited'])
            self.assertEqual(frame.time.dt.hour.tolist(),[5,4])
            self.assertEqual(stats['timestamp_offsets_seconds'],[18000.0])
            self.assertTrue(frame.power.isna().all())
            self.assertTrue(frame['mode'].eq('FT8').all())
            selected,_=read_archive(path,(61.5,-149),50,(32.5,-97),600,callsign=' tx2 ',chunk_size=2)
            self.assertEqual(selected.time.dt.hour.tolist(),[4,0])

    def test_reverse_only_archive_uses_A_receiver_and_keeps_actual_transmitter(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'day.hdf5'
            write_fixture(path)
            with h5py.File(path,'r+') as file:
                rows=file['Data/Table Layout'][:]
                for row in rows:
                    row['txlat'],row['rxlat']=row['rxlat'],row['txlat']
                    row['txlon'],row['rxlon']=row['rxlon'],row['txlon']
                    row['call_sign_tx'],row['call_sign_rx']=row['call_sign_rx'],row['call_sign_tx']
                file['Data/Table Layout'][:]=rows
            frame,_=read_archive(path,(61.5,-149),50,(32.5,-97),600,callsign='TX2',direction='Both directions',chunk_size=2)
            self.assertEqual(frame.time.dt.hour.tolist(),[4,0])
            self.assertTrue(frame.tx_sign.eq('RX1').all())
            self.assertTrue(frame.rx_sign.eq('TX2').all())
            forward,_=read_archive(path,(61.5,-149),50,(32.5,-97),600,direction='A → B')
            self.assertTrue(forward.empty)

    def test_missing_archive_and_invalid_schema_fail_clearly(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'missing.hdf5'
            with self.assertRaisesRegex(ValueError,'No download was attempted'):
                read_archive(path,(0,0),50,(0,0),50)
            with h5py.File(path,'w') as file:
                file.create_dataset('Data/Table Layout',data=np.zeros(1,dtype=[('wrong','i8')]))
            with self.assertRaisesRegex(ValueError,'schema'):
                read_archive(path,(0,0),50,(0,0),50)


if __name__=='__main__':
    unittest.main()
