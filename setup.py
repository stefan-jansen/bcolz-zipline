########################################################################
#
# License: BSD
#       Created: August 16, 2012
#       Author:  Francesc Alted - francesc@blosc.org
#
########################################################################

from sys import version_info as v

import numpy
import setuptools_scm  # noqa: F401
import toml  # noqa: F401
import os
from glob import glob
import sys
import platform
from setuptools import setup, Extension

# Check this Python version is supported
if any([(3,) < v < (3, 8)]):
    raise Exception("Unsupported Python version %d.%d. Requires Python > 3.7." % v[:2])

# For guessing the capabilities of the CPU for C-Blosc
try:
    # Currently just Intel and some ARM archs are supported by cpuinfo module
    import cpuinfo

    cpu_info = cpuinfo.get_cpu_info()
except:
    cpu_info = {'flags': []}

# Global variables
CFLAGS = os.environ.get('CFLAGS', '').split()
LFLAGS = os.environ.get('LFLAGS', '').split()
# Allow setting the Blosc dir if installed in the system
BLOSC_DIR = os.environ.get('BLOSC_DIR', '')

# Sources & libraries
inc_dirs = ['bcolz', numpy.get_include()]
lib_dirs = []
libs = []
def_macros = [("NPY_NO_DEPRECATED_API", "NPY_1_7_API_VERSION")]
sources = ['bcolz/carray_ext.pyx']

# Handle --blosc=[PATH] --lflags=[FLAGS] --cflags=[FLAGS]
args = sys.argv[:]
for arg in args:
    if arg.find('--blosc=') == 0:
        BLOSC_DIR = os.path.expanduser(arg.split('=')[1])
        sys.argv.remove(arg)
    if arg.find('--lflags=') == 0:
        LFLAGS = arg.split('=')[1].split()
        sys.argv.remove(arg)
    if arg.find('--cflags=') == 0:
        CFLAGS = arg.split('=')[1].split()
        sys.argv.remove(arg)

if BLOSC_DIR != '':
    # Using the Blosc library
    lib_dirs += [os.path.join(BLOSC_DIR, 'lib')]
    inc_dirs += [os.path.join(BLOSC_DIR, 'include')]
    libs += ['blosc']
else:
    # Compiling everything from sources
    sources += [f for f in glob('c-blosc/blosc/*.c')
                if 'avx2' not in f and 'sse2' not in f]
    sources += glob('c-blosc/internal-complibs/lz4*/*.c')
    sources += glob('c-blosc/internal-complibs/zlib*/*.c')
    sources += glob('c-blosc/internal-complibs/zstd*/*/*.c')
    inc_dirs += [os.path.join('c-blosc', 'blosc')]
    inc_dirs += [d for d in glob('c-blosc/internal-complibs/*')
                 if os.path.isdir(d)]
    inc_dirs += [d for d in glob('c-blosc/internal-complibs/zstd*/*')
                 if os.path.isdir(d)]
    def_macros += [('HAVE_LZ4', 1), ('HAVE_ZLIB', 1), ('HAVE_ZSTD', 1)]

    # Guess SSE2 or AVX2 capabilities
    # SSE2
    if 'DISABLE_BCOLZ_SSE2' not in os.environ and 'sse2' in cpu_info['flags']:
        print('SSE2 detected')
        CFLAGS.append('-DSHUFFLE_SSE2_ENABLED')
        sources += [f for f in glob('c-blosc/blosc/*.c') if 'sse2' in f]
        if os.name == 'posix':
            CFLAGS.append('-msse2')
        elif os.name == 'nt':
            def_macros += [('__SSE2__', 1)]

    # AVX2
    if 'DISABLE_BCOLZ_AVX2' not in os.environ and 'avx2' in cpu_info['flags']:
        print('AVX2 detected')
        CFLAGS.append('-DSHUFFLE_AVX2_ENABLED')
        sources += [f for f in glob('c-blosc/blosc/*.c') if 'avx2' in f]
        if os.name == 'posix':
            CFLAGS.append('-mavx2')
        elif os.name == 'nt':
            def_macros += [('__AVX2__', 1)]

# compile and link code instrumented for coverage analysis
if os.getenv('TRAVIS') and os.getenv('CI') and v[0:2] == (2, 7):
    CFLAGS.extend(["-fprofile-arcs", "-ftest-coverage"])
    LFLAGS.append("-lgcov")

CFLAGS.append('-std=gnu99')

# Disable Zstd assembly optimizations to resolve linking issues
# This addresses a problem with undefined references to assembly functions
# in certain build environments, particularly when compiling against numpy 2.0
# While this may slightly reduce Zstd compression/decompression performance,
# it significantly improves build compatibility across different systems
CFLAGS.append('-DZSTD_DISABLE_ASM')

setup(
    ext_modules=[Extension(
        name='bcolz.carray_ext',
        include_dirs=inc_dirs,
        define_macros=def_macros,
        sources=sources,
        library_dirs=lib_dirs,
        libraries=libs,
        extra_link_args=LFLAGS,
        extra_compile_args=CFLAGS
    )],
)
