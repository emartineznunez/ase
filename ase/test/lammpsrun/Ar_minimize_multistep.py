from ase.calculators.lammpsrun import LAMMPS
from ase.cluster.icosahedron import Icosahedron
from ase.data import atomic_numbers,  atomic_masses
import numpy as np
from numpy.testing import assert_allclose

ar_nc = Icosahedron('Ar', noshells=2)
ar_nc.cell = [[300, 0, 0], [0, 300, 0], [0, 0, 300]]
ar_nc.pbc = True

params = {}
params['pair_style'] = 'lj/cut 8.0'
params['pair_coeff'] = ['1 1 0.0108102 3.345']
params['masses'] = ['1 {}'.format(atomic_masses[atomic_numbers['Ar']])]

calc = LAMMPS(specorder=['Ar'], **params)

ar_nc.set_calculator(calc)
F1_ref = np.array([
    [+2.49366500e-17, +2.55871713e-17, +2.45029691e-17],
    [-1.41065856e-02, +4.33680869e-19, +8.71834934e-03],
    [-1.41065856e-02, +4.82469967e-18, -8.71834934e-03],
    [+1.41065856e-02, +7.04731412e-19, +8.71834934e-03],
    [+1.41065856e-02, +4.17417836e-18, -8.71834934e-03],
    [+8.71834934e-03, -1.41065856e-02, +5.69206141e-18],
    [-8.71834934e-03, -1.41065856e-02, +1.73472348e-18],
    [+8.71834934e-03, +1.41065856e-02, +5.74627151e-18],
    [-8.71834934e-03, +1.41065856e-02, +0.00000000e+00],
    [+8.67361738e-19, +8.71834934e-03, -1.41065856e-02],
    [+4.39101880e-18, -8.71834934e-03, -1.41065856e-02],
    [+4.87890978e-19, +8.71834934e-03, +1.41065856e-02],
    [+4.77048956e-18, -8.71834934e-03, +1.41065856e-02]])

pos1_ref = np.array([
    [3.16389502, 3.16389502, 3.16389502],
    [6.32779005, 3.16389502, 1.20850036],
    [6.32779005, 3.16389502, 5.11928968],
    [0.00000000, 3.16389502, 1.20850036],
    [0.00000000, 3.16389502, 5.11928968],
    [1.20850036, 6.32779005, 3.16389502],
    [5.11928968, 6.32779005, 3.16389502],
    [1.20850036, 0.00000000, 3.16389502],
    [5.11928968, 0.00000000, 3.16389502],
    [3.16389502, 1.20850036, 6.32779005],
    [3.16389502, 5.11928968, 6.32779005],
    [3.16389502, 1.20850036, 0.00000000],
    [3.16389502, 5.11928968, 0.00000000]])

assert_allclose(ar_nc.get_potential_energy(), -0.468147667942117)
assert_allclose(ar_nc.get_forces(), F1_ref, atol=1e-14)
assert_allclose(ar_nc.positions, pos1_ref, atol=1e-14)

params['minimize'] = '1.0e-15 1.0e-6 2000 4000'   # add minimize
calc.parameters = params

# set_atoms=True to read final coordinates after minimization
calc.run(set_atoms=True)

# get final coordinates after minimization
ar_nc.set_positions(calc.atoms.positions)

F2_ref = np.array([
    [-2.11091211e-18, +4.33680869e-18, +2.25514052e-17],
    [-2.42836577e-07, +3.42607887e-17, +1.50081258e-07],
    [-2.42836577e-07, +4.07117916e-17, -1.50081258e-07],
    [+2.42836577e-07, +2.90566182e-17, +1.50081258e-07],
    [+2.42836577e-07, +3.35018471e-17, -1.50081258e-07],
    [+1.50081258e-07, -2.42836577e-07, +1.71303943e-17],
    [-1.50081258e-07, -2.42836577e-07, +2.94902991e-17],
    [+1.50081258e-07, +2.42836577e-07, +1.49619900e-17],
    [-1.50081258e-07, +2.42836577e-07, +1.38777878e-17],
    [+4.29344060e-17, +1.50081258e-07, -2.42836577e-07],
    [+3.26344854e-17, -1.50081258e-07, -2.42836577e-07],
    [+9.75781955e-19, +1.50081258e-07, +2.42836577e-07],
    [+5.85469173e-18, -1.50081258e-07, +2.42836577e-07]])

pos2_ref = np.array([
    [3.16389502, 3.16389502, 3.16389502],
    [6.24218796, 3.16389502, 1.26140536],
    [6.24218796, 3.16389502, 5.06638468],
    [0.08560209, 3.16389502, 1.26140536],
    [0.08560209, 3.16389502, 5.06638468],
    [1.26140536, 6.24218796, 3.16389502],
    [5.06638468, 6.24218796, 3.16389502],
    [1.26140536, 0.08560209, 3.16389502],
    [5.06638468, 0.08560209, 3.16389502],
    [3.16389502, 1.26140536, 6.24218796],
    [3.16389502, 5.06638468, 6.24218796],
    [3.16389502, 1.26140536, 0.08560209],
    [3.16389502, 5.06638468, 0.08560209]])

assert_allclose(ar_nc.get_potential_energy(), -0.4791815887032201)
assert_allclose(ar_nc.get_forces(), F2_ref, atol=1e-14)
assert_allclose(ar_nc.positions, pos2_ref, atol=1e-14)
