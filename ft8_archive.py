"""Read the approved cached daily HamSCI file without loading it into memory."""
from datetime import date
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
from path_evidence import select_path

ARCHIVE_DATE=date(2024,5,12)
ARCHIVE_PATH=Path(__file__).resolve().parent/'.cache/madrigal/rsd2024-05-12.01.hdf5'
FT8_FREQUENCIES={1:1.840,3:3.573,5:5.357,7:7.074,10:10.136,14:14.074,18:18.100,21:21.074,24:24.915,28:28.074,50:50.313}
FIELDS=['year','month','day','hour','min','sec','ut1_unix','call_sign_tx','txlat','txlon','call_sign_rx','rxlat','rxlon','tfreq','sn','smode','ssrc']


def distances(lat,lon,latitudes,longitudes):
    a=np.sin(np.radians(latitudes-lat)/2)**2+np.cos(np.radians(lat))*np.cos(np.radians(latitudes))*np.sin(np.radians(longitudes-lon)/2)**2
    return 6371*2*np.arcsin(np.sqrt(np.clip(a,0,1)))


def decode(values):
    return np.char.upper(np.char.strip(values.astype('U')))


def calendar_times(rows):
    parts={target:pd.to_numeric(pd.Series(rows[source].astype('U8')),errors='coerce')
           for source,target in [('year','year'),('month','month'),('day','day'),('hour','hour'),('min','minute'),('sec','second')]}
    return pd.to_datetime(pd.DataFrame(parts),utc=True,errors='coerce')


def read_archive(path,tx_center,tx_radius,rx_center,rx_radius,limit=50000,callsign=None,chunk_size=100000,direction='A → B'):
    path=Path(path)
    if not path.is_file():
        raise ValueError('The approved May 12, 2024 FT8 archive is not cached on this installation. No download was attempted.')
    if not 1<=int(limit)<=100000 or not 1<=int(chunk_size)<=100000:
        raise ValueError('Invalid archive row or chunk limit.')
    for center,radius in [(tx_center,tx_radius),(rx_center,rx_radius)]:
        if not all(np.isfinite([*center,radius])) or not -90<=center[0]<=90 or not -180<=center[1]<=180 or not 0.1<=radius<=5500:
            raise ValueError('Invalid archive search circle.')
    columns=['time','band','rx_sign','rx_lat','rx_lon','rx_loc','tx_sign','tx_lat','tx_lon','tx_loc','frequency','power','snr','mode']
    kept=pd.DataFrame(columns=columns)
    stats={'date':str(ARCHIVE_DATE),'scanned_rows':0,'ft8_rows':0,'matching_rows':0,'limited':False,'file_bytes':path.stat().st_size}
    with h5py.File(path,'r') as file:
        if 'Data/Table Layout' not in file:
            raise ValueError('Archive is missing the expected Madrigal table.')
        table=file['Data/Table Layout']
        if not set(FIELDS).issubset(table.dtype.names or ()):
            raise ValueError('Archive fields differ from the verified HamSCI schema.')
        stats['total_rows']=len(table)
        sample=table.fields(FIELDS)[np.unique(np.linspace(0,len(table)-1,min(20,len(table)),dtype=int))] if len(table) else table.fields(FIELDS)[:0]
        labeled=calendar_times(sample)
        unix=pd.to_datetime(sample['ut1_unix'],unit='s',utc=True,errors='coerce')
        offsets=pd.Series((unix-labeled).dt.total_seconds()).dropna()
        stats['timestamp_offsets_seconds']=sorted(offsets.unique().tolist())
        stats['time_basis']='Archive UTC calendar fields'
        for start in range(0,len(table),chunk_size):
            block=table.fields(FIELDS)[start:start+chunk_size]
            stats['scanned_rows']+=len(block)
            mask=decode(block['smode'])=='FT8'
            stats['ft8_rows']+=int(mask.sum())
            mask &= np.isfinite(block['txlat']) & np.isfinite(block['txlon']) & np.isfinite(block['rxlat']) & np.isfinite(block['rxlon'])
            mask &= (np.abs(block['txlat'])<=90)&(np.abs(block['rxlat'])<=90)&(np.abs(block['txlon'])<=180)&(np.abs(block['rxlon'])<=180)
            forward_lat = (np.abs(block['txlat']-tx_center[0])<=np.degrees(tx_radius/6371)) & (np.abs(block['rxlat']-rx_center[0])<=np.degrees(rx_radius/6371))
            reverse_lat = (np.abs(block['rxlat']-tx_center[0])<=np.degrees(tx_radius/6371)) & (np.abs(block['txlat']-rx_center[0])<=np.degrees(rx_radius/6371))
            if direction=='A → B': reverse_lat &= False
            if direction=='B → A': forward_lat &= False
            rows=block[mask & (forward_lat | reverse_lat)]
            if not len(rows):
                continue
            frame=pd.DataFrame.from_records(rows)
            frame=frame.rename(columns={'call_sign_tx':'tx_sign','txlat':'tx_lat','txlon':'tx_lon','call_sign_rx':'rx_sign','rxlat':'rx_lat','rxlon':'rx_lon','tfreq':'frequency','sn':'snr'})
            frame['tx_sign']=decode(rows['call_sign_tx'])
            frame['rx_sign']=decode(rows['call_sign_rx'])
            frame=select_path(frame,tx_center,tx_radius,rx_center,rx_radius,distances,direction,callsign)
            frame['time']=calendar_times(rows).loc[frame.index]
            frame=frame[frame.time.dt.date.eq(ARCHIVE_DATE)&frame.tx_sign.ne('')&frame.rx_sign.ne('')].copy()
            mhz=frame.frequency.to_numpy(dtype=float)/1e6
            bands=np.array(list(FT8_FREQUENCIES))
            targets=np.array(list(FT8_FREQUENCIES.values()))
            best=np.argmin(np.abs(mhz[:,None]-targets[None,:]),axis=1)
            frame['band']=np.where(np.abs(mhz-targets[best])<0.5,bands[best],np.nan)
            frame=frame.dropna(subset=['band','snr'])
            frame=frame[np.isfinite(frame.snr)]
            frame['band']=frame.band.astype(int)
            frame['power']=np.nan
            frame['tx_loc']=frame['rx_loc']=''
            frame['mode']='FT8'
            stats['matching_rows']+=len(frame)
            if frame.empty:
                continue
            kept=pd.concat([kept,frame[columns]],ignore_index=True) if not kept.empty else frame[columns].copy()
            kept=kept.drop_duplicates(subset=['time','tx_sign','rx_sign','frequency','snr']).sort_values('time',ascending=False).head(int(limit))
    stats['limited']=stats['matching_rows']>limit
    stats['returned_rows']=len(kept)
    return kept.reset_index(drop=True),stats
