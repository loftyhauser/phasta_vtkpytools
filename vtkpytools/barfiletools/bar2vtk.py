import vtk, os, argparse
import pyvista as pv
import numpy as np
from pathlib import Path

from .data import binaryVelbar, binaryStsbar, calcReynoldsStresses, compute_vorticity
from ..common import globFile

def bar2vtk_parse():
    GeneralDescription="""Tool for putting velbar and stsbar data onto a vtm grid."""

    ModeDescription="""There are two modes: cli and toml. Run:
    \tbar2vtk cli --help
    \tbar2vtk toml --help

    to get help information for each mode respectively."""

    CLIDescription="""Set bar2vtk settings via cli arguments and flags.

    Examples:
    \tbar2vtk.py blankDataBlock.vtm BinaryBars 10000
    \tbar2vtk.py blankDataBlock.vtm BinaryBars 10000-20000 --ts0=500

    The name of the output file will be the same as the blank VTM file suffixed
    with the timestep requested. So in the first example above, the output would be
    "blankDataBlock_10000.vtm".

    Time Step Windows:
    ------------------
    Submit a timestep argument with a '-' in it to request a timestep window be
    generated. This requires a '--ts0' argument be provided as well for calculating
    the windowed value."""

    TomlDescription="""Set bar2vtk settings via a toml configuration file.

    Examples:
    \tbar2vtk toml filledBar2vtkConfig.toml
    \tbar2vtk toml --blank  #outputs 'blankConfig.toml'
    \tbar2vtk toml --blank customName.toml #outputs 'customName.toml' """

    class CustomFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
        """To display defaults in help and have a multiline help description"""
        # Shamelessly ripped from https://stackoverflow.com/a/18462760/7564988
        pass

    ## Parsing script input
    parser = argparse.ArgumentParser(description=GeneralDescription,
                                    formatter_class=CustomFormatter,
                                    prog='bar2vtk')
    subparser = parser.add_subparsers(title='Modes', description=ModeDescription, dest='subparser_name')

    # CLI Parser Setup
    cliparser = subparser.add_parser('cli', description=CLIDescription,
                                     formatter_class=CustomFormatter,
                                     help='Command Line Interface mode uses standard flags and cli arguments')
    # cliparser.set_defaults(which='cli')

    cliparser.add_argument('vtkfile', help="MultiBlock VTK file that contains 'grid' and 'wall'", type=Path)
    cliparser.add_argument('barfiledir', help='Path to *bar file directory', type=Path)
    cliparser.add_argument('timestep', help='Timestep of the barfiles. May be range', type=str)
    cliparser.add_argument('--ts0','--bar-average-start',
                        help='Starting timestep of the averaging process. Only used'
                            ' for generating windows.',
                        type=int, default=-1)
    cliparser.add_argument('-f','--new-file-prefix',
                        help='Prefix for the new file. Will have timestep appended.',
                        type=str)
    cliparser.add_argument('--outpath', help='Custom path for the output VTM file.'
                                        ' vtkfile path used if not given', type=Path)
    cliparser.add_argument('--velonly', help='Only process velbar file', action='store_true')
    cliparser.add_argument('--debug', help='Load raw stsbar data into VTM', action='store_true')
    cliparser.add_argument('--vptpath', help='Custom path to vtkpytools package', type=Path)
    cliparser.add_argument('-a', '--ascii', help='Read *bar files as ASCII', action='store_true')
    cliparser.add_argument('--velbar', help='Path to velbar file(s)', type=Path, nargs='+', default=[])
    cliparser.add_argument('--stsbar', help='Path to stsbar file(s)', type=Path, nargs='+', default=[])

    # Toml Parser Setup
    tomlparser = subparser.add_parser('toml', description=TomlDescription, formatter_class=CustomFormatter,
                                      help='Toml mode uses configuration files in the toml format')
    # tomlparser.set_defaults(which='toml')
    tomlsubparser = tomlparser.add_subparsers()
    blanktoml = tomlsubparser.add_parser('blank', description='Blank Toml', formatter_class=CustomFormatter,
                                      help='Create blank toml')
    blanktoml.set_defaults(blank=True)
    tomlparser.add_argument('tomlfile', nargs='?', help='Run bar2vtk using toml config file')

    args = vars(parser.parse_args())

    return args

def bar2vtk(vtkfile: Path, barfiledir: Path, timestep: str, \
            ts0: int=-1,  new_file_prefix: str='', outpath: Path=None, \
            velonly=False, debug=False, asciibool=False, \
            velbar=[],     stsbar=[]):
    """Convert velbar and stsbar files into 2D vtk files

    See bar2vtk_commandline help documentation for more information.

    Parameters
    ----------
    vtkfile : Path
        vtkfile
    barfiledir : Path
        barfiledir
    timestep : str
        timestep
    ts0 : int
        ts0
    new_file_prefix : str
        new_file_prefix
    outpath : Path
        outpath
    velonly :
        velonly
    debug :
        debug
    asciibool :
        asciibool
    velbar :
        velbar
    stsbar :
        stsbar
    """

    ## ---- Process/check script arguments
    assert vtkfile.is_file()
    assert barfiledir.is_dir()

    if debug and velonly:
        raise RuntimeError('--velonly counteracts the effect of --debug. Choose one or the other.')

    if not len(velbar) == len(stsbar):
        raise ValueError('--velbar and --stsbar must be given same number of paths'
                        ', given {} and {}, respectively'.format(len(velbar), len(stsbar)))

    for flag, arg in {'--velbar':velbar, '--stsbar':stsbar}.items():
        if len(arg) > 2:
            pathStrings = '\n\t' + '\n\t'.join([x.as_posix() for x in arg])
            raise ValueError('{} can only contain two paths max.'
                            ' The following were given:{}'.format(flag, pathStrings))

        if len(arg) == 2 and not '-' in timestep:
            raise ValueError('{} was given two paths, but timestep was not given range.'.format(flag))

    if new_file_prefix:
        vtmName = Path(new_file_prefix + '_' + timestep + '.vtm')
    else:
        vtmName = Path(os.path.splitext(vtkfile.name)[0] + '_' + timestep + '.vtm')

    vtmPath = (outpath if outpath else vtkfile.parent) / vtmName

    velbarReader = np.loadtxt if asciibool else binaryVelbar
    stsbarReader = np.loadtxt if asciibool else binaryStsbar

    ## ---- Loading data arrays
    if '-' in timestep:
    # Create timestep windows
        if ts0 == -1:
            raise RuntimeError("Starting timestep of bar field averaging required (--ts0)")

        timesteps = [int(x) for x in timestep.split('-')]
        print('Creating timewindow between {} and {}'.format(timesteps[0], timesteps[1]))
        if not velbar:
            velbarPaths = []; stsbarPaths = []
            for timestep in timesteps:
                velbarPaths.append(globFile('velbar*.{}*'.format(timestep), barfiledir))

                if not velonly:
                    stsbarPaths.append(globFile('stsbar*.{}*'.format(timestep), barfiledir))
        else:
            velbarPaths = velbar
            stsbarPaths = stsbar

        print('Using data files:\n\t{}\t{}'.format(velbarPaths[0], velbarPaths[1]))
        if not velonly:
            print('\t{}\t{}'.format(stsbarPaths[0], stsbarPaths[1]))

        velbarArrays = []; stsbarArrays = []
        for i in range(2):
            velbarArrays.append(velbarReader(velbarPaths[i]))
            if not velonly:
                stsbarArrays.append(stsbarReader(stsbarPaths[i]))

        velbarArray = (velbarArrays[1]*(timesteps[1] - ts0) -
                    velbarArrays[0]*(timesteps[0] - ts0)) / (timesteps[1] - timesteps[0])
        if not velonly:
            stsbarArray = (stsbarArrays[1]*(timesteps[1] - ts0) -
                        stsbarArrays[0]*(timesteps[0] - ts0)) / (timesteps[1] - timesteps[0])
        print('Finished computing timestep window')
    else:
        velbarPath = velbar if velbar else \
            (globFile('velbar*.{}*'.format(timestep), barfiledir))
        print('Using data files:\n\t{}'.format(velbarPath))
        velbarArray = velbarReader(velbarPath)

        if not velonly:
            stsbarPath = stsbar if stsbar else \
                (globFile('stsbar*.{}*'.format(timestep), barfiledir))
            print('\t{}'.format(stsbarPath))
            stsbarArray = stsbarReader(stsbarPath)

    ## ---- Load DataBlock
    dataBlock = pv.MultiBlock(vtkfile.as_posix())
    grid = dataBlock['grid']
    wall = dataBlock['wall']

    ## ---- Load *bar data into dataBlock
    grid['Pressure'] = velbarArray[:,0]
    grid['Velocity'] = velbarArray[:,1:4]

    if not velonly:
        ReyStrTensor = calcReynoldsStresses(stsbarArray, velbarArray)
        grid['ReynoldsStress'] = ReyStrTensor

    if debug and not velonly:
        grid['stsbar'] = stsbarArray

    grid = grid.compute_gradient(scalars='Velocity')
    grid = compute_vorticity(grid, scalars='Velocity')

    ## ---- Copy data from grid to wall object
    wall = wall.sample(grid)

    dataBlock['grid'] = grid
    dataBlock['wall'] = wall
    print('Saving dataBlock file to: {}'.format(vtmPath), end='')
    dataBlock.save(vtmPath)
    print('\tDone!')