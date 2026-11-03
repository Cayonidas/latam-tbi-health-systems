import numpy as np

def residualize(a,g1,g2):
    a=a.astype(float).copy()
    for iteration in range(200):
        prev=a.copy()
        for g in [g1,g2]:
            sums=np.zeros((int(g.max())+1,a.shape[1]))
            np.add.at(sums,g,a)
            a-= (sums/np.bincount(g)[:,None])[g]
        if np.max(np.abs(a-prev))<1e-10: return a,iteration+1
    raise RuntimeError('Fixed effects did not converge')
