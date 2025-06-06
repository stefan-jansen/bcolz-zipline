########################################################################
#
#       License: BSD
#       Created: September 01, 2010
#       Author:  Francesc Alted - francesc@blosc.org
#
########################################################################

from __future__ import absolute_import

from collections import namedtuple, OrderedDict
from itertools import islice
import json
from keyword import iskeyword
import os
import re
import shutil
import sys

import numpy as np
import bcolz
from bcolz import utils, attrs, array2string
from .py2help import _inttypes, _strtypes, imap, izip, xrange

_inttypes += (np.integer,)

ROOTDIRS = '__rootdirs__'

re_ident = re.compile(r"^[^\d\W]\w*$", re.UNICODE)
# re_str_split = re.compile("^\s+|\s*,\s*|\s+$")
re_str_split = re.compile(r"^\s+|\s*,\s*|\s+$")

def validate_names(columns, keyword='names'):
    if not all([is_identifier(x) and not iskeyword(x) for x in columns]):
        raise ValueError("column {0} must be valid Python identifiers, and must "
                         "not start with an underscore".format(keyword))
    return list(map(str, columns))


def is_identifier(x):
    # python 3 has str.isidentifier
    return re_ident.match(x)


def split_string(x):
    # replicates the namedtuple behavior for string splitting on spaces
    # and commas and calling str on names
    # does not check for identifiers as keywords. that's done in validate_names.
    return re_str_split.split(str(x))


class cols(object):
    """Class for accessing the columns on the ctable object."""

    def __init__(self, rootdir, mode):
        self.rootdir = rootdir
        self.mode = mode
        self.names = []
        self._cols = {}

    def read_meta_and_open(self):
        """Read the meta-information and initialize structures."""
        # Get the directories of the columns
        rootsfile = os.path.join(self.rootdir, ROOTDIRS)
        with open(rootsfile, 'rb') as rfile:
            data = json.loads(rfile.read().decode('ascii'))
        # JSON returns unicode, but we want plain bytes for Python 2.x
        self.names = [str(name) for name in data['names']]
        # Initialize the cols by instantiating the carrays
        for name in self.names:
            dir_ = os.path.join(self.rootdir, name)
            self._cols[name] = bcolz.carray(rootdir=dir_, mode=self.mode)

    def update_meta(self):
        """Update metainfo about directories on-disk."""
        if not self.rootdir:
            return
        data = {'names': self.names}
        rootsfile = os.path.join(self.rootdir, ROOTDIRS)
        with open(rootsfile, 'wb') as rfile:
            rfile.write(json.dumps(data).encode('ascii'))
            rfile.write(b"\n")

    def __getitem__(self, name):
        return self._cols[name]

    def __setitem__(self, name, carray):
        if len(self.names) and len(carray) != len(self._cols[self.names[0]]):
            raise ValueError(
                "new column length is inconsistent with ctable")
        dtype = None
        cparams = bcolz.defaults.cparams
        if name in self.names:
            # Column already exists.  Overwrite it, but keep the same dtype
            # and cparams than the previous column.
            dtype = self._cols[name].dtype
            cparams = self._cols[name].cparams
        else:
            self.names.append(name)
        # All columns should be a carray
        if type(carray) != bcolz.carray:
            try:
                rd = os.path.join(self.rootdir, name) if self.rootdir else None
                carray = bcolz.carray(carray, rootdir=rd, mode=self.mode,
                                      dtype=dtype, cparams=cparams)
            except:
                raise ValueError(
                    "`%s` cannot be converted into a carray object "
                    "of the correct type" % carray)
        self._cols[name] = carray
        self.update_meta()

    def __iter__(self):
        return iter(self._cols)

    def __len__(self):
        return len(self.names)

    def insert(self, name, pos, carray):
        """Insert carray in the specified pos and name."""
        self.names.insert(pos, name)
        self._cols[name] = carray
        self.update_meta()

    def pop(self, name):
        """Return the named column and remove it."""
        pos = self.names.index(name)
        name = self.names.pop(pos)
        col = self._cols.pop(name)
        self.update_meta()
        return col

    def __str__(self):
        fullrepr = ""
        for name in self.names:
            fullrepr += "%s : %s" % (name, str(self._cols[name]))
        return fullrepr

    def __repr__(self):
        fullrepr = ""
        for name in self.names:
            fullrepr += "%s : %s\n" % (name, repr(self._cols[name]))
        return fullrepr


class ctable(object):
    """This class represents a compressed, column-wise table.

    Create a new ctable from `cols` with optional `names`.

    Parameters
    ----------
    columns : tuple or list of column objects
        The list of column data to build the ctable object.  These are
        typically carrays, but can also be a list of NumPy arrays or a pure
        NumPy structured array.  A list of lists or tuples is valid too, as
        long as they can be converted into carray objects.
    names : list of strings or string
        The list of names for the columns.  The names in this list must be
        valid Python identifiers, must not start with an underscore, and has
        to be specified in the same order as the `cols`.  If not passed, the
        names will be chosen as 'f0' for the first column, 'f1' for the second
        and so on so forth (NumPy convention).
    kwargs : list of parameters or dictionary
        Allows to pass additional arguments supported by carray
        constructors in case new carrays need to be built.

    Notes
    -----
    Columns passed as carrays are not be copied, so their settings
    will stay the same, even if you pass additional arguments (cparams,
    chunklen...).

    """

    # Properties
    # ``````````

    @property
    def cbytes(self):
        "The compressed size of this object (in bytes)."
        return self._get_stats()[1]

    @property
    def cparams(self):
        "The compression parameters for this object."
        return self._cparams

    @property
    def dtype(self):
        "The data type of this object (numpy dtype)."
        names, cols = self.names, self.cols
        l = []
        for name in names:
            col = cols[name]
            # Need to account for multidimensional columns
            t = (name, col.dtype) if col.ndim == 1 else \
                (name, (col.dtype, col.shape[1:]))
            l.append(t)
        return np.dtype(l)

    @property
    def names(self):
        "The column names of the object (list)."
        return self.cols.names

    @property
    def ndim(self):
        "The number of dimensions of this object."
        return len(self.shape)

    @property
    def nbytes(self):
        "The original (uncompressed) size of this object (in bytes)."
        return self._get_stats()[0]

    @property
    def shape(self):
        "The shape of this object."
        return (self.len,)

    @property
    def size(self):
        "The size of this object."
        return np.prod(self.shape)

    def __init__(self, columns=None, names=None, **kwargs):

        # Important optional params
        self._cparams = kwargs.get('cparams', bcolz.cparams())
        self.rootdir = kwargs.get('rootdir', None)
        if self.rootdir is not None:
            self.auto_flush = kwargs.pop('auto_flush', True)
        else:
            self.auto_flush = False
            # We actually need to pop it from the kwargs, so it doesn't get
            # passed down to the carray.
            try:
                kwargs.pop('auto_flush')
            except KeyError:
                pass
        "The directory where this object is saved."
        if self.rootdir is None and columns is None:
            raise ValueError(
                "You should pass either a `columns` or a `rootdir` param"
                " at very least")
        # The mode in which the object is created/opened
        if self.rootdir is not None and os.path.exists(self.rootdir):
            self.mode = kwargs.setdefault('mode', 'a')
            if columns is not None and self.mode == 'a':
                raise ValueError(
                    "You cannot pass a `columns` param in 'a'ppend mode.\n"
                    "(If you are trying to create a new ctable, perhaps the "
                    "directory exists already.)")
        else:
            self.mode = kwargs.setdefault('mode', 'w')

        # Setup the columns accessor
        self.cols = cols(self.rootdir, self.mode)
        "The ctable columns accessor."

        # The length counter of this array
        self.len = 0

        # Create a new ctable or open it from disk
        _new = False
        if self.mode in ('r', 'a'):
            self._open_ctable()
        elif columns is not None:
            self._create_ctable(columns, names, **kwargs)
            _new = True
        else:
            raise ValueError(
                "You cannot open a ctable in 'w'rite mode"
                " without a `columns` param")

        # Attach the attrs to this object
        self.attrs = attrs.attrs(self.rootdir, self.mode, _new=_new)

        # Cache a structured array of len 1 for ctable[int] acceleration
        self._arr1 = np.empty(shape=(1,), dtype=self.dtype)

    def _create_ctable(self, columns, names, **kwargs):
        """Create a ctable anew."""

        # Create the rootdir if necessary
        if self.rootdir:
            self._mkdir_rootdir(self.rootdir, self.mode)

        # Get the names of the columns
        if names is None:
            if isinstance(columns, np.ndarray):  # ratype case
                names = list(columns.dtype.names)
            else:
                names = ["f%d" % i for i in range(len(columns))]
        else:
            if type(names) == tuple:
                names = list(names)
            if type(names) != list:
                raise ValueError(
                    "`names` can only be a list or tuple")
            if len(names) != len(columns):
                raise ValueError(
                    "`columns` and `names` must have the same length")
        # Check names validity. Cast to string.
        names = validate_names(names)

        # Guess the kind of columns input
        calist, nalist, ratype = False, False, False
        if type(columns) in (tuple, list):
            calist = all(isinstance(v, bcolz.carray) for v in columns)
            nalist = all(isinstance(v, np.ndarray) for v in columns)
        elif isinstance(columns, np.ndarray):
            ratype = hasattr(columns.dtype, "names")
            if ratype:
                if len(columns.shape) != 1:
                    raise ValueError("only unidimensional shapes supported")
        else:
            raise ValueError("`columns` input is not supported")

        # Populate the columns
        clen = -1
        for i, name in enumerate(names):
            if self.rootdir:
                # Put every carray under each own `name` subdirectory
                kwargs['rootdir'] = os.path.join(self.rootdir, name)
            if calist:
                column = columns[i]
                if self.rootdir:
                    # Store this in destination
                    column = column.copy(**kwargs)
            elif nalist:
                column = columns[i]
                if column.dtype == np.void:
                    raise ValueError(
                        "`columns` elements cannot be of type void")
                column = bcolz.carray(column, **kwargs)
            elif ratype:
                column = bcolz.carray(columns[name], **kwargs)
            else:
                # Try to convert from a sequence of columns
                column = bcolz.carray(columns[i], **kwargs)
            self.cols[name] = column
            if clen >= 0 and clen != len(column):
                if self.rootdir:
                    shutil.rmtree(self.rootdir)
                raise ValueError("all `columns` must have the same length")
            clen = len(column)

        self.len = clen

        if self.auto_flush:
            self.flush()

    def _open_ctable(self):
        """Open an existing ctable on-disk."""
        if self.mode == 'r' and not os.path.exists(self.rootdir):
            raise KeyError(
                "Disk-based ctable opened with `r`ead mode "
                "yet `rootdir='{rootdir}'` does not exist".format(
                    rootdir=self.rootdir,
                )
            )

        # Open the ctable by reading the metadata
        self.cols.read_meta_and_open()

        # Get the length out of the first column
        self.len = len(self.cols[self.names[0]])

    def _mkdir_rootdir(self, rootdir, mode):
        """Create the `self.rootdir` directory safely."""
        if os.path.exists(rootdir):
            if mode != "w":
                raise IOError(
                    "specified rootdir path '%s' already exists "
                    "and creation mode is '%s'" % (rootdir, mode))
            if os.path.isdir(rootdir):
                shutil.rmtree(rootdir)
            else:
                os.remove(rootdir)
        os.mkdir(rootdir)

    def append(self, cols):
        """Append `cols` to this ctable.

        Parameters
        ----------
        cols : list/tuple of scalar values, NumPy arrays or carrays
            It also can be a NumPy record, a NumPy recarray, or
            another ctable.

        """

        # Guess the kind of cols input
        calist, nalist, sclist, ratype = False, False, False, False
        if type(cols) in (tuple, list):
            calist = all(isinstance(v, bcolz.carray) for v in cols)
            nalist = all(isinstance(v, np.ndarray) for v in cols)
            if not (calist or nalist):
                # Try with a scalar list
                sclist = True
        elif isinstance(cols, np.ndarray):
            ratype = hasattr(cols.dtype, "names")
        elif isinstance(cols, np.void):
            ratype = hasattr(cols.dtype, "names")
            sclist = True
        elif isinstance(cols, bcolz.ctable):
            # Convert into a list of carrays
            cols = [cols[name] for name in self.names]
            calist = True
        else:
            raise ValueError("`cols` input is not supported")
        if not (calist or nalist or sclist or ratype):
            raise ValueError("`cols` input is not supported")

        # Populate the columns
        clen = -1
        for i, name in enumerate(self.names):
            if calist or sclist:
                column = cols[i]
            elif nalist:
                column = cols[i]
                if column.dtype == np.void:
                    raise ValueError("`cols` elements cannot be of type void")
                column = column
            elif ratype:
                column = cols[name]
            # Append the values to column
            self.cols[name].append(column)
            if sclist and not hasattr(column, '__len__'):
                clen2 = 1
            else:
                if isinstance(column, _strtypes):
                    clen2 = 1
                else:
                    clen2 = len(column)
            if clen >= 0 and clen != clen2:
                raise ValueError(
                    "all cols in `cols` must have the same length")
            clen = clen2
        self.len += clen

        if self.auto_flush:
            self.flush()

    def trim(self, nitems):
        """Remove the trailing `nitems` from this instance.

        Parameters
        ----------
        nitems : int
            The number of trailing items to be trimmed.

        """

        for name in self.names:
            self.cols[name].trim(nitems)
        self.len -= nitems

    def resize(self, nitems):
        """Resize the instance to have `nitems`.

        Parameters
        ----------
        nitems : int
            The final length of the instance.  If `nitems` is larger than the
            actual length, new items will appended using `self.dflt` as
            filling values.

        """

        for name in self.names:
            self.cols[name].resize(nitems)
        self.len = nitems

    def addcol(self, newcol, name=None, pos=None, move=False, **kwargs):
        """Add a new `newcol` object as column.

        Parameters
        ----------
        newcol : carray, ndarray, list or tuple
            If a carray is passed, no conversion will be carried out.
            If conversion to a carray has to be done, `kwargs` will
            apply.
        name : string, optional
            The name for the new column.  If not passed, it will
            receive an automatic name.
        pos : int, optional
            The column position.  If not passed, it will be appended
            at the end.
        move: boolean, optional
            If the new column is an existing, disk-based carray should it
            a) copy the data directory (False) or
            b) move the data directory (True)
        kwargs : list of parameters or dictionary
            Any parameter supported by the carray constructor.

        Notes
        -----
        You should not specificy both `name` and `pos` arguments,
        unless they are compatible.

        See Also
        --------
        delcol

        """

        # Check params
        if pos is None:
            pos = len(self.names)
        else:
            if pos and type(pos) != int:
                raise ValueError("`pos` must be an int")
            if pos < 0 or pos > len(self.names):
                raise ValueError("`pos` must be >= 0 and <= len(self.cols)")
        if name is None:
            name = "f%d" % pos
        else:
            if not isinstance(name, _strtypes):
                raise ValueError("`name` must be a string")
        if name in self.names:
            raise ValueError("'%s' column already exists" % name)
        if len(newcol) != self.len:
            raise ValueError("`newcol` must have the same length than ctable")

        if self.rootdir is not None:
            col_rootdir = os.path.join(self.rootdir, name)
            kwargs.setdefault('rootdir', col_rootdir)

        kwargs.setdefault('cparams', self.cparams)

        if (isinstance(newcol, bcolz.carray) and
            self.rootdir is not None and
            newcol.rootdir is not None):
            # a special case, where you have a disk-based carray is inserted in a disk-based ctable
            if move:  # move the the carray
                shutil.move(newcol.rootdir, col_rootdir)
                newcol.rootdir = col_rootdir
            else:  # copy the the carray
                newcol = newcol.copy(rootdir=col_rootdir)
        elif isinstance(newcol, (np.ndarray, bcolz.carray)):
            newcol = bcolz.carray(newcol, **kwargs)
        elif type(newcol) in (list, tuple):
            newcol = bcolz.carray(newcol, **kwargs)
        elif type(newcol) != bcolz.carray:
            raise ValueError(
                """`newcol` type not supported""")

        # Insert the column
        self.cols.insert(name, pos, newcol)

        # Update _arr1 for the new dtype
        self._arr1 = np.empty(shape=(1,), dtype=self.dtype)

        if self.auto_flush:
            self.flush()

    def delcol(self, name=None, pos=None, keep=False):
        """Remove the column named `name` or in position `pos`.

        Parameters
        ----------
        name: string, optional
            The name of the column to remove.
        pos: int, optional
            The position of the column to remove.
        keep: boolean
            For disk-backed columns: keep the data on disk?

        Notes
        -----
        You must specify at least a `name` or a `pos`.  You should not
        specify both `name` and `pos` arguments, unless they are
        compatible.

        See Also
        --------
        addcol

        """

        if name is None and pos is None:
            raise ValueError("specify either a `name` or a `pos`")
        if name is not None and pos is not None:
            raise ValueError("you cannot specify both a `name` and a `pos`")
        if name:
            if not isinstance(name, _strtypes):
                raise ValueError("`name` must be a string")
            if name not in self.names:
                raise ValueError("`name` not found in columns")
            pos = self.names.index(name)
        elif pos is not None:
            if type(pos) != int:
                raise ValueError("`pos` must be an int")
            if pos < 0 or pos > len(self.names):
                raise ValueError("`pos` must be >= 0 and <= len(self.cols)")
            name = self.names[pos]

        # Remove the column
        col = self.cols.pop(name)

        # Update _arr1 for the new dtype (only if it is non-empty)
        if self.dtype != np.dtype([]):
            self._arr1 = np.empty(shape=(1,), dtype=self.dtype)

        if not keep:
            col.purge()

        if self.auto_flush:
            self.flush()

    def copy(self, **kwargs):
        """Return a copy of this ctable.

        Parameters
        ----------
        kwargs : list of parameters or dictionary
            Any parameter supported by the carray/ctable constructor.

        Returns
        -------
        out : ctable object
            The copy of this ctable.

        """

        # Check that origin and destination do not overlap
        rootdir = kwargs.get('rootdir', None)
        if rootdir and self.rootdir and rootdir == self.rootdir:
                raise IOError("rootdir cannot be the same during copies")

        # Remove possible unsupported args for columns
        names = kwargs.pop('names', self.names)

        # Copy the columns
        if rootdir:
            # A copy is always made during creation with a rootdir
            cols = [self.cols[name] for name in self.names]
        else:
            cols = [self.cols[name].copy(**kwargs) for name in self.names]
        # Create the ctable
        ccopy = ctable(cols, names, **kwargs)
        return ccopy

    @staticmethod
    def fromdataframe(df, **kwargs):
        """Return a ctable object out of a pandas dataframe.

        Parameters
        ----------
        df : DataFrame
            A pandas dataframe.
        kwargs : list of parameters or dictionary
            Any parameter supported by the ctable constructor.

        Returns
        -------
        out : ctable object
            A ctable filled with values from `df`.

        Notes
        -----
        The 'object' dtype will be converted into a 'S'tring type, if possible.
        This allows for much better storage savings in bcolz.

        See Also
        --------
        ctable.todataframe

        """
        if bcolz.pandas_here:
            import pandas as pd
        else:
            raise ValueError("you need pandas to use this functionality")

        # Use the names in kwargs, or if not there, the names in dataframe
        if 'names' in kwargs:
            names = kwargs.pop('names')
        else:
            names = list(df.columns.values)

        # Build the list of columns as in-memory numpy arrays and carrays
        # (when doing the conversion object -> string)
        cols = []
        # Remove a possible rootdir argument to prevent copies going to disk
        ckwargs = kwargs.copy()
        if 'rootdir' in ckwargs:
            del ckwargs['rootdir']
        for key in names:
            vals = df[key].values  # just a view as a numpy array
            if vals.dtype == object:
                inferred_type = pd.api.types.infer_dtype(vals)
                if inferred_type == 'unicode':
                    maxitemsize = max(len(i) for i in vals)
                    col = bcolz.carray(vals,
                                       dtype='U%d' % maxitemsize, **ckwargs)
                elif inferred_type == 'string':
                    maxitemsize = max(len(i) for i in vals)
                    # In Python 3 strings should be represented as Unicode
                    dtype = "U"
                    col = bcolz.carray(vals, dtype='%s%d' %
                                       (dtype, maxitemsize), **ckwargs)
                else:
                    col = vals
                cols.append(col)
            else:
                cols.append(vals)

        # Create the ctable
        ct = ctable(cols, names, **kwargs)
        return ct

    @staticmethod
    def fromhdf5(filepath, nodepath='/ctable', **kwargs):
        """Return a ctable object out of a compound HDF5 dataset (PyTables Table).

        Parameters
        ----------
        filepath : string
            The path of the HDF5 file.
        nodepath : string
            The path of the node inside the HDF5 file.
        kwargs : list of parameters or dictionary
            Any parameter supported by the ctable constructor.

        Returns
        -------
        out : ctable object
            A ctable filled with values from the HDF5 node.

        See Also
        --------
        ctable.tohdf5

        """
        if bcolz.tables_here:
            import tables as tb
        else:
            raise ValueError("you need PyTables to use this functionality")

        # Read the Table on file
        f = tb.open_file(filepath)
        try:
            t = f.get_node(nodepath)
        except:
            f.close()
            raise

        # Use the names in kwargs, or if not there, the names in Table
        names = kwargs.pop('names') if 'names' in kwargs else t.colnames
        # Add the `expectedlen` param if necessary
        if 'expectedlen' not in kwargs:
            kwargs['expectedlen'] = len(t)

        # Collect metadata
        dtypes = [t.dtype.fields[name][0] for name in names]
        cols = [np.zeros(0, dtype=dt) for dt in dtypes]
        # Create an empty ctable
        ct = ctable(cols, names, **kwargs)
        # Fill it chunk by chunk
        bs = t._v_chunkshape[0]
        for i in xrange(0, len(t), bs):
            ct.append(t[i:i+bs])
        # Get the attributes
        for key in t.attrs._f_list():
            ct.attrs[key] = t.attrs[key]
        f.close()
        return ct

    def todataframe(self, columns=None, orient='columns'):
        """Return a pandas dataframe out of this object.

        Parameters
        ----------
        columns : sequence of column labels, optional
            Must be passed if orient='index'.
        orient : {'columns', 'index'}, default 'columns'
            The "orientation" of the data. If the keys of the input correspond
            to column labels, pass 'columns' (default). Otherwise if the keys
            correspond to the index, pass 'index'.

        Returns
        -------
        out : DataFrame
            A pandas DataFrame filled with values from this object.

        See Also
        --------
        ctable.fromdataframe

        """
        if bcolz.pandas_here:
            import pandas as pd
        else:
            raise ValueError("you need pandas to use this functionality")

        if orient == 'index':
            keys = self.names
        else:
            keys = self.names if columns is None else columns
            columns = None
        # Use a generator here to minimize the number of column copies
        # existing simultaneously in-memory
        df = pd.DataFrame.from_dict(
            OrderedDict((key, self[key][:]) for key in keys),
            columns=columns, orient=orient)
        return df

    def tohdf5(self, filepath, nodepath='/ctable', mode='w',
               cparams=None, cname=None):
        """Write this object into an HDF5 file.

        Parameters
        ----------
        filepath : string
            The path of the HDF5 file.
        nodepath : string
            The path of the node inside the HDF5 file.
        mode : string
            The mode to open the PyTables file.  Default is 'w'rite mode.
        cparams : cparams object
            The compression parameters.  The defaults are the same than for
            the current bcolz environment.
        cname : string
            Any of the compressors supported by PyTables (e.g. 'zlib').  The
            default is to use 'blosc' as meta-compressor in combination with
            one of its compressors (see `cparams` parameter above).

        See Also
        --------
        ctable.fromhdf5

        """
        if bcolz.tables_here:
            import tables as tb
        else:
            raise ValueError("you need PyTables to use this functionality")

        if os.path.exists(filepath):
            raise IOError("path '%s' already exists" % filepath)

        f = tb.open_file(filepath, mode=mode)
        cparams = cparams if cparams is not None else bcolz.defaults.cparams
        cname = cname if cname is not None else "blosc:"+cparams['cname']
        filters = tb.Filters(complevel=cparams['clevel'],
                             shuffle=cparams['clevel'],
                             complib=cname)
        t = f.create_table(f.root, nodepath[1:], self.dtype, filters=filters,
                           expectedrows=len(self))
        # Set the attributes
        for key, val in self.attrs:
            t.attrs[key] = val
        # Copy the data
        for block in bcolz.iterblocks(self):
            t.append(block)
        f.close()

    def __len__(self):
        return self.len

    def __sizeof__(self):
        return self.cbytes

    def _dtype_fromoutcols(self, outcols):
        if outcols is None:
            dtype = self.dtype
        else:
            if not isinstance(outcols, (list, tuple)):
                raise ValueError("only a sequence is supported for outcols")
            # Get the dtype for the outcols set
            try:
                dtype = [(name, self[name].dtype) for name in outcols]
            except IndexError:
                raise ValueError(
                    "Some names in `outcols` are not actual column names")
        return dtype

    def _ud(self, user_dict):
        """Update a user_dict with columns, locals and globals."""
        d = user_dict.copy()
        d.update(self.cols._cols)
        f = sys._getframe(2)
        d.update(f.f_globals)
        d.update(f.f_locals)
        return d

    def _check_outcols(self, outcols):
        """Check outcols."""
        if outcols is None:
            outcols = self.names
        else:
            if type(outcols) not in (list, tuple) + _strtypes:
                raise ValueError("only list/str is supported for outcols")
            if isinstance(outcols, _strtypes):
                outcols = split_string(outcols)
            # Check name validity
            outcols = validate_names(outcols, 'outcols')
            if (set(outcols) - set(self.names+['nrow__'])):
                raise ValueError("outcols doesn't match names")
        return outcols

    def where(self, expression, outcols=None, limit=None, skip=0,
              out_flavor=namedtuple, user_dict={}, vm=None):
        """Iterate over rows where `expression` is true.

        Parameters
        ----------
        expression : string or carray
            A boolean Numexpr expression or a boolean carray.
        outcols : list of strings or string
            The list of column names that you want to get back in results.
            Alternatively, it can be specified as a string such as 'f0 f1' or
            'f0, f1'.  If None, all the columns are returned.  If the special
            name 'nrow__' is present, the number of row will be included in
            output.
        limit : int
            A maximum number of elements to return.  The default is return
            everything.
        skip : int
            An initial number of elements to skip.  The default is 0.
        out_flavor : namedtuple, tuple or ndarray
            Whether the returned rows are namedtuples or tuples.  Default are
            named tuples.
        user_dict : dict
            An user-provided dictionary where the variables in expression
            can be found by name.
        vm : string
            The virtual machine to be used in computations.  It can be
            'numexpr', 'python' or 'dask'.  The default is to use 'numexpr' if
            it is installed.

        Returns
        -------
        out : iterable

        See Also
        --------
        iter

        """

        # Check input
        if isinstance(expression, _strtypes):
            # That must be an expression
            boolarr = self.eval(expression, user_dict=self._ud(user_dict),
                                vm=vm)
        elif hasattr(expression, "dtype") and expression.dtype.kind == 'b':
            boolarr = expression
        else:
            raise ValueError(
                "only boolean expressions or arrays are supported")

        # Check outcols
        outcols = self._check_outcols(outcols)

        # Get iterators for selected columns
        icols, dtypes = [], []
        for name in outcols:
            if name == "nrow__":
                icols.append(boolarr.wheretrue(limit=limit, skip=skip))
                dtypes.append((name, np.int_))
            else:
                col = self.cols[name]
                icols.append(col.where(boolarr, limit=limit, skip=skip))
                dtypes.append((name, col.dtype))
        dtype = np.dtype(dtypes)
        return self._iter(icols, dtype, out_flavor)

    def fetchwhere(self, expression, outcols=None, limit=None, skip=0,
                   out_flavor=None, user_dict={}, vm=None, **kwargs):
        """Fetch the rows fulfilling the `expression` condition.

        Parameters
        ----------
        expression : string or carray
            A boolean Numexpr expression or a boolean carray.
        outcols : list of strings or string
            The list of column names that you want to get back in results.
            Alternatively, it can be specified as a string such as 'f0 f1' or
            'f0, f1'.  If None, all the columns are returned.  If the special
            name 'nrow__' is present, the number of row will be included in
            output.
        limit : int
            A maximum number of elements to return.  The default is return
            everything.
        skip : int
            An initial number of elements to skip.  The default is 0.
        out_flavor : string
            The flavor for the `out` object.  It can be 'bcolz' or 'numpy'.
            If None, the value is get from `bcolz.defaults.out_flavor`.
        user_dict : dict
            An user-provided dictionary where the variables in expression
            can be found by name.
        vm : string
            The virtual machine to be used in computations.  It can be
            'numexpr', 'python' or 'dask'.  The default is to use 'numexpr' if
            it is installed.
        kwargs : list of parameters or dictionary
            Any parameter supported by the carray constructor.

        Returns
        -------
        out : bcolz or numpy object
            The outcome of the expression.  In case out_flavor='bcolz', you
            can adjust the properties of this object by passing any additional
            arguments supported by the carray constructor in `kwargs`.

        See Also
        --------
        whereblocks

        """
        if out_flavor is None:
            out_flavor = bcolz.defaults.out_flavor

        if out_flavor == "numpy":
            it = self.whereblocks(expression, len(self), outcols, limit, skip,
                                  user_dict=self._ud(user_dict), vm=vm)
            return next(it)
        elif out_flavor in ("bcolz", "carray"):
            dtype = self._dtype_fromoutcols(outcols)
            it = self.where(expression, outcols, limit, skip,
                            out_flavor=tuple, user_dict=self._ud(user_dict),
                            vm=vm)
            ct = bcolz.fromiter(it, dtype, count=-1, **kwargs)
            ct.flush()
            return ct
        else:
            raise ValueError(
                "`out_flavor` can only take 'bcolz' or 'numpy values")


    def whereblocks(self, expression, blen=None, outcols=None, limit=None,
                    skip=0, user_dict={}, vm=None):
        """Iterate over the rows that fullfill the `expression` condition on
        this ctable, in blocks of size `blen`.

        Parameters
        ----------
        expression : string or carray
            A boolean Numexpr expression or a boolean carray.
        blen : int
            The length of the block that is returned.  The default is the
            chunklen, or for a ctable, the minimum of the different column
            chunklens.
        outcols : list of strings or string
            The list of column names that you want to get back in results.
            Alternatively, it can be specified as a string such as 'f0 f1' or
            'f0, f1'.  If None, all the columns are returned.  If the special
            name 'nrow__' is present, the number of row will be included in
            output.
        limit : int
            A maximum number of elements to return.  The default is return
            everything.
        skip : int
            An initial number of elements to skip.  The default is 0.
        user_dict : dict
            An user-provided dictionary where the variables in expression
            can be found by name.
        vm : string
            The virtual machine to be used in computations.  It can be
            'numexpr', 'python' or 'dask'.  The default is to use 'numexpr' if
            it is installed.

        Returns
        -------
        out : iterable
            The iterable returns numpy objects of blen length.

        See Also
        --------
        See :py:func:`<bcolz.toplevel.iterblocks>` in toplevel functions.

        """

        if blen is None:
            # Get the minimum chunklen for every field
            blen = min(self[col].chunklen for col in self.cols)

        outcols = self._check_outcols(outcols)
        dtype = self._dtype_fromoutcols(outcols)
        it = self.where(expression, outcols, limit, skip, out_flavor=tuple,
                        user_dict=self._ud(user_dict), vm=vm)
        return self._iterwb(it, blen, dtype)

    def _iterwb(self, it, blen, dtype):
        while True:
            ra = np.fromiter(islice(it, blen), dtype)
            if len(ra) == 0:
                break
            yield ra

    def __iter__(self):
        return self.iter(0, self.len, 1)

    def iter(self, start=0, stop=None, step=1, outcols=None,
             limit=None, skip=0, out_flavor=namedtuple):
        """Iterator with `start`, `stop` and `step` bounds.

        Parameters
        ----------
        start : int
            The starting item.
        stop : int
            The item after which the iterator stops.
        step : int
            The number of items incremented during each iteration.  Cannot be
            negative.
        outcols : list of strings or string
            The list of column names that you want to get back in results.
            Alternatively, it can be specified as a string such as 'f0 f1' or
            'f0, f1'.  If None, all the columns are returned.  If the special
            name 'nrow__' is present, the number of row will be included in
            output.
        limit : int
            A maximum number of elements to return.  The default is return
            everything.
        skip : int
            An initial number of elements to skip.  The default is 0.
        out_flavor : namedtuple, tuple or ndarray
            Whether the returned rows are namedtuples or tuples.  Default are
            named tuples.

        Returns
        -------
        out : iterable

        See Also
        --------
        where

        """

        outcols = self._check_outcols(outcols)

        # Check limits
        if step <= 0:
            raise NotImplementedError("step param can only be positive")
        start, stop, step = slice(start, stop, step).indices(self.len)

        # Get iterators for selected columns
        icols, dtypes = [], []
        for name in outcols:
            if name == "nrow__":
                istop = None
                if limit is not None:
                    istop = limit + skip
                icols.append(islice(xrange(start, stop, step), skip, istop))
                dtypes.append((name, np.int_))
            else:
                col = self.cols[name]
                icols.append(
                    col.iter(start, stop, step, limit=limit, skip=skip))
                dtypes.append((name, col.dtype))
        dtype = np.dtype(dtypes)
        return self._iter(icols, dtype, out_flavor)

    def _iter(self, icols, dtype, out_flavor):
        """Return a list of `icols` iterators with `dtype` names."""

        icols = tuple(icols)
        if out_flavor is namedtuple or out_flavor == "namedtuple":
            namedt = namedtuple('row', dtype.names)
            iterable = imap(namedt, *icols)
        elif out_flavor is np.ndarray or out_flavor == "ndarray":
            def setarr1(*val):
                ret = self._arr1.copy()
                ret.__setitem__(0, val)
                return ret
            iterable = imap(setarr1, *icols)
        else:
            # A regular tuple (fastest)
            iterable = izip(*icols)
        return iterable

    def _where(self, boolarr, colnames=None):
        """Return rows where `boolarr` is true as an structured array.

        This is called internally only, so we can assum that `boolarr`
        is a boolean array.
        """

        if colnames is None:
            colnames = self.names
        cols = [self.cols[name][boolarr] for name in colnames]
        dtype = np.dtype([(name, self.cols[name].dtype) for name in colnames])
        result = np.rec.fromarrays(cols, dtype=dtype).view(np.ndarray)

        return result

    def __getitem__(self, key):
        """Returns values based on `key`.

        All the functionality of ``ndarray.__getitem__()`` is supported
        (including fancy indexing), plus a special support for expressions.

        Parameters
        ----------
        key : string
            The corresponding ctable column name will be returned.  If
            not a column name, it will be interpret as a boolean
            expression (computed via `ctable.eval`) and the rows where
            these values are true will be returned as a NumPy
            structured array.

        See Also
        --------
        ctable.eval

        """

        # Check for an empty dtype
        if self.dtype == np.dtype([]):
            raise KeyError("cannot retrieve data from a ctable with no columns")

        # First, check for integer
        if isinstance(key, _inttypes):
            # Get a copy of the len-1 array
            ra = self._arr1.copy()
            # Fill it
            ra[0] = tuple([self.cols[name][key] for name in self.names])
            return ra[0]
        # Slices
        elif type(key) == slice:
            (start, stop, step) = key.start, key.stop, key.step
            if step and step <= 0:
                raise NotImplementedError("step in slice can only be positive")
        # Multidimensional keys
        elif isinstance(key, tuple):
            if len(key) != 1:
                raise IndexError("multidimensional keys are not supported")
            return self[key[0]]
        # List of integers (case of fancy indexing), or list of column names
        elif type(key) is list:
            if len(key) == 0:
                return np.empty(0, self.dtype)
            strlist = all(isinstance(v, _strtypes) for v in key)
            # Range of column names
            if strlist:
                cols = [self.cols[name] for name in key]
                return ctable(cols, key)
            # Try to convert to a integer array
            try:
                key = np.array(key, dtype=np.int_)
            except:
                raise IndexError(
                    "key cannot be converted to an array of indices")
            return np.fromiter((self[i] for i in key),
                               dtype=self.dtype, count=len(key))
        # A boolean array (case of fancy indexing)
        elif hasattr(key, "dtype"):
            if key.dtype.type == np.bool_:
                return self._where(key)
            elif np.issubdtype(key.dtype, np.integer):
                # An integer array
                return np.array([tuple(self[i]) for i in key], dtype=self.dtype)
            else:
                raise IndexError(
                    "arrays used as indices must be integer (or boolean)")
        # Column name or expression
        elif isinstance(key, _strtypes):
            if key not in self.names:
                # key is not a column name, try to evaluate
                arr = self.eval(key, user_dict=self._ud({}))
                if arr.dtype.type != np.bool_:
                    raise IndexError(
                        "`key` %s does not represent a boolean "
                        "expression" % key)
                return self._where(arr)
            return self.cols[key]
        # All the rest not implemented
        else:
            raise NotImplementedError("key not supported: %s" % repr(key))

        # From now on will only deal with [start:stop:step] slices

        # Get the corrected values for start, stop, step
        (start, stop, step) = slice(start, stop, step).indices(self.len)
        # Build a numpy container
        n = utils.get_len_of_range(start, stop, step)
        ra = np.empty(shape=(n,), dtype=self.dtype)
        # Fill it
        for name in self.names:
            ra[name][:] = self.cols[name][start:stop:step]

        return ra

    def __setitem__(self, key, value):
        """Sets values based on `key`.

        All the functionality of ``ndarray.__setitem__()`` is supported
        (including fancy indexing), plus a special support for expressions.

        Parameters
        ----------
        key : string, int, tuple, slice
            If string and it matches a column name, this will be set to
            `value`.  If string, but not a column name, it will be
            interpreted as a boolean expression (computed via `ctable.eval`)
            and the rows where these values are true will be set to `value`.
            If int or slice, then the corresponding rows will be set to
            `value`.

        value : object
            The values to be set.

        See Also
        --------
        ctable.eval

        """
        # Check for an empty dtype
        if self.dtype == np.dtype([]):
            raise KeyError("cannot assign to ctable with no columns")

        if isinstance(key, (bytes, str)):
            # First, check if the key is a column name
            if key in self.names:
                # Yes, so overwrite it
                self.cols[key] = value
                return

        # Else, convert value into a structured array
        value = utils.to_ndarray(value, self.dtype)
        # Check if key is a condition actually
        if isinstance(key, (bytes, str)):
            # Convert key into a boolean array
            # key = self.eval(key)
            # The method below is faster (specially for large ctables)
            rowval = 0
            for nrow in self.where(key, outcols=["nrow__"]):
                nrow = nrow[0]
                if len(value) == 1:
                    for name in self.names:
                        self.cols[name][nrow] = value[name]
                else:
                    for name in self.names:
                        self.cols[name][nrow] = value[name][rowval]
                    rowval += 1
            return

        # key should int or slice, so modify the rows
        for name in self.names:
            self.cols[name][key] = value[name]
        return

    def eval(self, expression, **kwargs):
        """Evaluate the `expression` on columns and return the result.

        Parameters
        ----------
        expression : string
            A string forming an expression, like '2*a+3*b'. The values
            for 'a' and 'b' are variable names to be taken from the
            calling function's frame.  These variables may be column
            names in this table, scalars, carrays or NumPy arrays.
        kwargs : list of parameters or dictionary
            Any parameter supported by the `eval()` top level function.

        Returns
        -------
        out : bcolz object
            The outcome of the expression.  You can tailor the
            properties of this object by passing additional arguments
            supported by the carray constructor in `kwargs`.

        See Also
        --------
        eval (top level function)

        """
        # Call top-level eval with cols, locals and gloabls as user_dict
        user_dict = kwargs.pop('user_dict', {})
        return bcolz.eval(expression, user_dict=self._ud(user_dict), **kwargs)

    def flush(self):
        """Flush data in internal buffers to disk.

        This call should typically be done after performing modifications
        (__settitem__(), append()) in persistence mode.  If you don't do this,
        you risk losing part of your modifications.

        """
        for name in self.names:
            self.cols[name].flush()

    def free_cachemem(self):
        """Get rid of internal caches to free memory.

        This call can typically be made after reading from a
        carray/ctable so as to free the memory used internally to
        cache data blocks/chunks.

        """
        for name in self.names:
            self.cols[name].free_cachemem()

    def _get_stats(self):
        """Get some stats (nbytes, cbytes and ratio) about this object.

        Returns
        -------
        out : a (nbytes, cbytes, ratio) tuple
            nbytes is the number of uncompressed bytes in ctable.
            cbytes is the number of compressed bytes.  ratio is the
            compression ratio.

        """

        nbytes, cbytes = 0, 0
        names, cols = self.names, self.cols
        for name in names:
            column = cols[name]
            nbytes += column.nbytes
            cbytes += column.cbytes
        cratio = nbytes / float(cbytes) if cbytes > 0 else np.nan
        return (nbytes, cbytes, cratio)

    def __str__(self):
        if self.dtype == np.dtype([]):
            return ""
        else:
            return array2string(self)

    def __repr__(self):
        nbytes, cbytes, cratio = self._get_stats()
        snbytes = utils.human_readable_size(nbytes)
        scbytes = utils.human_readable_size(cbytes)
        header = "ctable(%s, %s)\n" % (self.shape, self.dtype)
        header += "  nbytes: %s; cbytes: %s; ratio: %.2f\n" % (
            snbytes, scbytes, cratio)
        header += "  cparams := %r\n" % self.cparams
        if self.rootdir:
            header += "  rootdir := '%s'\n" % self.rootdir
        fullrepr = header + str(self)
        return fullrepr

    def __enter__(self):
        return self

    def __exit__(self, type, value, tb):
        if self.mode != 'r':
            self.flush()

# Local Variables:
# mode: python
# tab-width: 4
# fill-column: 78
# End:
