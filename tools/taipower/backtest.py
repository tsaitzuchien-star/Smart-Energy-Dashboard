"""回測：台電 15 分鐘需量（每小時最大）＋ Open-Meteo 前一天預報（缺的用 ERA5），逐日／逐週重新擬合，看隔日上班日最高需量誤差。
路徑寫死在下面，離線分析用，非排程程式。"""
import sys, json, glob, numpy as np, pandas as pd
from datetime import date, timedelta
sys.path.insert(0,'/home/user/sed')
from calendar_tw import load_calendar
from forecast import tou_period
cal=load_calendar({2023,2024,2025,2026})
t=pd.read_csv('/mnt/user-data/uploads/Taipower_Dashboard/taipower_data.csv'); t['s']=pd.to_datetime(t['時間'])-pd.Timedelta(minutes=15)
t['date']=t.s.dt.date; t['hour']=t.s.dt.hour
H=t.groupby(['date','hour']).需量_kW.max().rename('y').reset_index()
V={'T':'temperature_2m','RH':'relative_humidity_2m','CC':'cloud_cover','R':'shortwave_radiation','P':'precipitation'}
def load(pat,suf):
    fr=[]
    for f in sorted(glob.glob(pat)):
        h=json.load(open(f))['hourly'];d=pd.DataFrame({'time':pd.to_datetime(h['time'])})
        for k,v in V.items(): d[k]=h[v+suf]
        fr.append(d)
    d=pd.concat(fr).drop_duplicates('time');d['date']=d.time.dt.date;d['hour']=d.time.dt.hour;return d.drop(columns='time')
era=load('/tmp/claude-0/wx/era5_*.json','');prev=load('/tmp/claude-0/wx/prev_*.json','_previous_day1')
prev=prev.dropna()
for k in V: era[k]=era[k].astype(float); prev[k]=prev[k].astype(float)
wx=era.set_index(['date','hour']);pv=prev.set_index(['date','hour']);ix=pv.index.intersection(wx.index);wx.loc[ix,list(V)]=pv.loc[ix,list(V)].values
wx=wx.reset_index();wx['fc']=wx.set_index(['date','hour']).index.isin(pv.index)
D=H.merge(wx,on=['date','hour'])
D['hol']=D.date.map(cal.is_holiday);D['dt']=np.where(D.hol,'off','work')
D['magoff']=[(not h) and hr<=18 and tou_period(d,hr)=='尖峰' for d,hr,h in zip(D.date,D.hour,D.hol)]
D['doy']=pd.to_datetime(D.date).dt.dayofyear
F=list(V)
def fit(tr,ref,hl,seas,alpha=3.0,magkw=75.0):
    age=np.array([(ref-d).days for d in tr.date]);w=0.5**(age/hl)
    if seas:
        dd=np.abs(tr.doy.values-pd.Timestamp(ref).dayofyear);dd=np.minimum(dd,365-dd)
        w=w+seas*np.exp(-dd**2/(2*21**2))*0.5**(age/365)*(age>60)
    hrs=sorted(tr.hour.unique());X=np.zeros((len(tr),len(hrs)+len(F)))
    X[np.arange(len(tr)),[hrs.index(h) for h in tr.hour]]=1
    mu=np.average(tr[F].values,axis=0,weights=w);sd=np.sqrt(np.average((tr[F].values-mu)**2,axis=0,weights=w));sd[sd==0]=1
    X[:,len(hrs):]=(tr[F].values-mu)/sd;y=tr.y.values+magkw*tr.magoff.values
    A=(X*w[:,None]).T@X;b=(X*w[:,None]).T@y
    for k in range(len(hrs),A.shape[0]):A[k,k]+=alpha*w.sum()/len(tr)
    beta=np.linalg.solve(A,b);return hrs,beta,mu,sd
def pred(m,te,magkw=75.0):
    hrs,beta,mu,sd=m;idx=[hrs.index(h) if h in hrs else -1 for h in te.hour]
    p=np.array([beta[i] if i>=0 else np.nan for i in idx])+((te[F].values-mu)/sd)@beta[len(hrs):]
    return p-magkw*te.magoff.values
def run(hours,hl,seas,start=date(2025,10,16),end=date(2026,11,22),fc_only=True):
    out=[];d=start
    S=D[D.hour.isin(hours)]
    while d<=end:
        mon=d-timedelta(days=d.weekday())   # model refit each Monday using data before Monday
        tr=S[S.date<mon]
        wk=S[(S.date>=d)&(S.date<d+timedelta(days=7-d.weekday()))]
        for dt in ('work','off'):
            trd=tr[tr.dt==dt];te=wk[wk.dt==dt]
            if len(te)==0:continue
            m=fit(trd,mon-timedelta(days=1),hl,seas);te=te.assign(p=pred(m,te));out.append(te)
        d=d+timedelta(days=7-d.weekday())
    o=pd.concat(out)
    if fc_only: o=o[o.groupby('date').fc.transform('all')]
    return o
def score(o,name,hours_peak=range(7,19)):
    r={}
    for dt in ('work','off'):
        s=o[(o.dt==dt)&o.hour.isin(hours_peak)];g=s.groupby('date').agg(a=('y','max'),p=('p','max'))
        e=g.a-g.p;r[dt]=(len(g),round(e.abs().mean(),1),round(e.mean(),1),round(np.percentile(e,95),1),round((e.abs()/g.a).mean()*100,1))
        s2=o[o.dt==dt];r[dt+'_hrMAE']=round((s2.y-s2.p).abs().mean(),1)
    print(name,r,flush=True);return r
if __name__=='__main__':
    for name,hours,hl,seas in [('V0 07-18 hl21',range(7,19),21,0),('V0b 07-18 hl42',range(7,19),42,0),
                               ('V2 07-18 hl21+season',range(7,19),21,0.5),('V2b 07-18 hl21+season1',range(7,19),21,1.0)]:
        score(run(list(hours),hl,seas),name)
    for name,hl,seas in [('V1 0-23 hl21',21,0),('V3 0-23 hl21+season',21,0.5)]:
        o=run(list(range(24)),hl,seas);score(o,name);score(o,name+' [全日最高]',range(24))
    # persistence baseline: same daytype last week same weekday
    g=D[D.hour.isin(range(7,19))].groupby(['date','dt']).y.max().reset_index();g['prev']=g.date.map(dict(zip(g.date+timedelta(days=7),g.y)))
    g=g[(g.date>=date(2025,10,16))&g.prev.notna()&(g.dt=='work')];print('persistence work peak MAE',round((g.y-g.prev).abs().mean(),1),len(g))

def run_daily(hours,hl,start=date(2025,10,16),end=date(2026,11,22)):
    out=[];S=D[D.hour.isin(hours)];d=start
    while d<=end:
        tr=S[S.date<d];te=S[S.date==d]
        if len(te):
            dt=te.dt.iloc[0];m=fit(tr[tr.dt==dt],d-timedelta(days=1),hl,0);out.append(te.assign(p=pred(m,te)))
        d+=timedelta(days=1)
    o=pd.concat(out);return o[o.groupby('date').fc.transform('all')]
def biascorr(o,n=20,hours_peak=range(7,19),q=0.5):
    s=o[o.hour.isin(hours_peak)];g=s.groupby(['date','dt']).agg(a=('y','max'),p=('p','max')).reset_index().sort_values('date')
    g['e']=g.a-g.p;g['cr']=g.groupby('dt').e.transform(lambda x:x.shift(1).rolling(n,min_periods=5).quantile(q))
    g['p2']=g.p+g.cr.fillna(0);g['e2']=g.a-g.p2
    for dt in ('work','off'):
        x=g[g.dt==dt];print('  biascorr',dt,'MAE',round(x.e2.abs().mean(),1),'bias',round(x.e2.mean(),1),'P95',round(np.percentile(x.e2,95),1),'MAPE',round((x.e2.abs()/x.a).mean()*100,1),flush=True)
    return g
