import numpy as np
import vtk
from .core import *
from scipy.io import FortranFile
import warnings

def getBinaryVelbar(velbarPath, velbar_ncols=5):
    """Get velbar array from binary file.

    Args:
        velbarPath (Path): Path to velbar file.
        velbar_ncols (uint): Number of columns in the binary file. (default: 5)
    """
    velbar = FortranFile(velbarPath, 'r')
    velbarArray = velbar.read_reals()
    velbar_nrows = int(velbarArray.shape[0]/velbar_ncols)
    velbarArray = np.reshape(velbarArray, (velbar_nrows, velbar_ncols))

    return velbarArray

def getBinaryStsbar(stsbarPath, stsbar_ncols=6):
    """Get stsbar array from binary file.

    Args:
        stsbarPath (Path): Path to stsbar file.
        stsbar_ncols (uint): Number of columns in the binary file. (default: 6)
    """
    stsbar = FortranFile(stsbarPath, 'r')
    stsbarArray = stsbar.read_reals()
    stsbar_nrows = int(stsbarArray.shape[0]/stsbar_ncols)
    stsbarArray = np.reshape(stsbarArray, (stsbar_nrows, stsbar_ncols))
    return stsbarArray

def calcReynoldsStresses(stsbarArray, velbarArray, conservative_stresses=False):
    """Calculate Reynolds Stresses from velbar and stsbar data.

    Args:
        stsbarArray (ndarray): Array of stsbar values
        velbarArray (ndarray): Array of velbar values
        conservative_stresses (bool): Whether the stsbar file used the
            'Conservative Stresses' option (default:False)
    """
    if conservative_stresses:
        warnings.warn("Calculation of Reynolds Stresses when using the 'Conservative Stress' option for stsbar has not been validated.")
        ReyStrTensor = np.empty((stsbarArray.shape[0], 6))
        ReyStrTensor[:,0] = stsbarArray[:,3] - stsbarArray[:,0]**2
        ReyStrTensor[:,1] = stsbarArray[:,4] - stsbarArray[:,1]**2
        ReyStrTensor[:,2] = stsbarArray[:,5] - stsbarArray[:,2]**2
        ReyStrTensor[:,3] = stsbarArray[:,6] - stsbarArray[:,0]*stsbarArray[:,1]
        ReyStrTensor[:,4] = stsbarArray[:,7] - stsbarArray[:,1]*stsbarArray[:,2]
        ReyStrTensor[:,5] = stsbarArray[:,8] - stsbarArray[:,0]*stsbarArray[:,2]
        # ReyStrTensor[:,5] = np.zeros_like(ReyStrTensor[:,5])
    else:
        ReyStrTensor = np.empty((stsbarArray.shape[0], 6))

        ReyStrTensor[:,0] = stsbarArray[:,0] - velbarArray[:,1]**2
        ReyStrTensor[:,1] = stsbarArray[:,1] - velbarArray[:,2]**2
        ReyStrTensor[:,2] = stsbarArray[:,2] - velbarArray[:,3]**2
        ReyStrTensor[:,3] = stsbarArray[:,3] - velbarArray[:,1]*velbarArray[:,2]
        ReyStrTensor[:,4] = stsbarArray[:,4] - velbarArray[:,1]*velbarArray[:,3]
        ReyStrTensor[:,5] = stsbarArray[:,5] - velbarArray[:,2]*velbarArray[:,3]

    return ReyStrTensor

def calcCf(wall, Uref, nu=1.5E-5, rho=1, planeNormal='XY'):

    if 'Normals' not in wall.array_names:
        raise RuntimeError('The wall object must have a "Normals" field present.')
    mu = nu * rho
    # streamwise_vectors = np.array((wall['Normals'][:,1],
    #                                 -wall['Normals'][:,0],
    #                                 np.zeros_like(wall['Normals'][:,0]))).T

        # reshape the gradient such that is is an array of rank 2 tensors
    grad_tensors = wall['gradient'].reshape(wall['gradient'].shape[0], 3, 3)
        # Compute gradient vector tangential to the wall
    tangentialVelocityGradient = np.einsum('ijk,ik->ij', grad_tensors, wall['Normals'])

    # Tw = np.einsum('ij,ij->i', tangential_e_ij, streamwise_vectors)*mu
    if planeNormal == 'XY'.lower():
        planeNormal = np.array([0,0,1])
    elif planeNormal == 'XZ'.lower():
        planeNormal = np.array([0,1,0])
    elif planeNormal == 'YZ'.lower():
        planeNormal = np.array([1,0,0])

        # Project tangential gradient vector onto the chosen plane using n x (T_w x n)
    Tw = mu * np.cross(planeNormal[None,:],
                       np.cross(tangentialVelocityGradient, planeNormal[None,:]))
    Tw = np.linalg.norm(Tw, axis=1)

    Cf = Tw / (0.5*rho*Uref**2)
    return Cf

def compute_vorticity(dataset, scalars, vorticity_name='vorticity'):
    """Compute Vorticity, only needed till my PR gets merged"""
    alg = vtk.vtkGradientFilter()

    alg.SetComputeVorticity(True)
    alg.SetVorticityArrayName(vorticity_name)

    _, field = dataset.get_array(scalars, preference='point', info=True)
    # args: (idx, port, connection, field, name)
    alg.SetInputArrayToProcess(0, 0, 0, field.value, scalars)
    alg.SetInputData(dataset)
    alg.Update()
    return pv.filters._get_output(alg)

def sampleDataBlockProfile(dataBlock, line_walldists, pointid=None, cutterObj=None):
    "Return a sampled line at the wall point index"

    wall = dataBlock['wall']

    if 'Normals' not in wall.array_names:
        raise RuntimeError('The wall object must have a "Normals" field present.')

    if pointid:
        wallnormal = wall['Normals'][pointid,:]
        wallnormal = np.tile(wallnormal, (len(line_walldists),1))

        sample_points = line_walldists[:, None] * wallnormal
        sample_points += wall.points[pointid]

        sample_line = linesFromPoints(sample_points)
        sample_line = sample_line.sample(dataBlock['grid'])
        sample_line['WallDistance'] = line_walldists

    if cutterObj:
        cutter = vtk.vtkCutter()
        cutter.SetCutFunction(cutterObj)
        cutter.SetInputData(wall)
        cutter.Update()
        cutterout = pv.wrap(cutter.GetOutput())

        if cutterout.points.shape[0] != 1:
            raise RuntimeError('vtkCutter resulted in %d points instead of 1.'.format(
                cutterout.points.shape[0]))

        wallnormal = cutterout['Normals']

        sample_points = line_walldists[:, None] * wallnormal
        sample_points += cutterout.points

        sample_line = linesFromPoints(sample_points)
        sample_line = sample_line.sample(dataBlock['grid'])
        sample_line['WallDistance'] = line_walldists

    return sample_line

def calcBoundaryLayerStats(dataBlock, line_walldists, dpercent=False,
                                dvortpercent=False, velocity_component=0, Uref=1, nu=1):

    if dpercent and isinstance(dpercent, bool): dpercent = 0.95
    if dvortpercent and isinstance(dvortpercent, bool): dvortpercent = 0.95

    wall = dataBlock['wall']
    delta_mom = np.zeros(wall.points.shape[0])
    Re_theta = np.zeros(wall.points.shape[0])
    delta_displace = np.zeros(wall.points.shape[0])
    for i, point in enumerate(wall.points):
        wallnormal = wall['Normals'][i,:]
        wallnormal = np.tile(wallnormal, (len(line_walldists),1))
        sample_points = line_walldists[:, None] * wallnormal
        sample_points += point

        sampled = False
        attempt = 0
        tolerance = 1E-8
        nudge_increment = 1E-8
        nudge_size = 0
        while not sampled:
            # Sample domain
            sample_line = linesFromPoints(sample_points)
            sample_line = sample_line.sample(dataBlock['grid'])

            # Nudge sample_line back and forth until line is within domain
                # Primarily needed at end points
            if np.abs(sample_line['Velocity']).max() < 1E-8:
                if attempt == 0:
                    print(f'Could not get a sample line for index {i}. Will attempt nudging')
                elif attempt == 8:
                    warnings.warn(f'Nudging failed for index {i}! The last nudge size was {nudge_size}.\n')
                    break

                orig_nudge_size = nudge_size
                attempt += 1
                if attempt % 2 == 0:
                    nudge_size = (attempt / 2) * nudge_increment - nudge_size
                else:
                    nudge_size = -np.ceil(attempt / 2) * nudge_increment - nudge_size
                sample_points[:,0] += nudge_size

            else:
                sampled = True
                if attempt > 0:
                    print(f'Nudging index {i} was successful after {attempt} attempts.')
                    print(f'\tThe nudge size was {nudge_size + orig_nudge_size}.')

        Ue = sample_line['Velocity'][-1,velocity_component]
        U = sample_line['Velocity'][:,velocity_component]

        integrand_displace = 1 - U/Ue
        integrand_mom = integrand_displace * (U/Ue)
        delta_displace[i] = np.trapz(integrand_displace, line_walldists)
        delta_mom[i] = np.trapz(integrand_mom, line_walldists)
        Re_theta[i] = delta_mom[i]*Uref/nu

        # U_vort = cumtrapz(sample_line['vorticity'][:,2], line_walldists, initial=0)
        # Uinf_vort = U_vort[-1]
        # delta_vortpercentIndex = line_walldists[U_vort > dvortpercent*U_vort[-1] ]
        # if not line_walldists.size > 0:
        #     warnings.warn('Could not find U_vort value. Try increasing the range of search or adjusting the dvortpercent value.')
        # else:
        # test = False

    dataBlock['wall']['delta_displace'] = delta_displace
    dataBlock['wall']['delta_mom'] = delta_mom
    dataBlock['wall']['Re_theta'] = Re_theta

    return dataBlock

