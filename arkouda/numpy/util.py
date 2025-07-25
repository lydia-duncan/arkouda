from __future__ import annotations

import builtins
import json
from math import prod as maprod
from typing import TYPE_CHECKING, List, Literal, Sequence, Tuple, TypeVar, Union, cast

from typeguard import typechecked

from arkouda.categorical import Categorical
from arkouda.client import generic_msg, get_config, get_mem_used
from arkouda.client_dtypes import BitVector, BitVectorizer, IPv4
from arkouda.groupbyclass import GroupBy
from arkouda.infoclass import list_registry
from arkouda.numpy.dtypes import (
    _is_dtype_in_union,
    dtype,
    float_scalars,
    int_scalars,
    numeric_scalars,
)
from arkouda.numpy.pdarrayclass import create_pdarray, pdarray
from arkouda.numpy.pdarraycreation import arange
from arkouda.numpy.pdarraysetops import unique
from arkouda.numpy.sorting import coargsort
from arkouda.numpy.strings import Strings
from arkouda.numpy.timeclass import Datetime, Timedelta

if TYPE_CHECKING:
    from arkouda.index import Index
    from arkouda.numpy.segarray import SegArray
    from arkouda.pandas.series import Series
else:
    Index = TypeVar("Index")
    Series = TypeVar("Series")
    SegArray = TypeVar("SegArray")


def identity(x):
    return x


def get_callback(x):
    if type(x) in {Datetime, Timedelta, IPv4}:
        return type(x)
    elif hasattr(x, "_cast"):
        return x._cast
    elif isinstance(x, BitVector):
        return BitVectorizer(width=x.width, reverse=x.reverse)
    else:
        return identity


def generic_concat(items, ordered=True):
    # this version can be called with Dataframe and Series (which have Class.concat methods)
    from arkouda.numpy.pdarraysetops import concatenate as pdarrayconcatenate

    types = {type(x) for x in items}
    if len(types) != 1:
        raise TypeError(f"Items must all have same type: {types}")
    t = types.pop()

    if t is list:
        return [x for lst in items for x in lst]

    return (
        t.concat(items, ordered=ordered)
        if hasattr(t, "concat")
        else pdarrayconcatenate(items, ordered=ordered)
    )


def report_mem(pre=""):
    cfg = get_config()
    used = get_mem_used() / (cfg["numLocales"] * cfg["physicalMemory"])
    print(f"{pre} mem use: {get_mem_used() / (1024**4): .2f} TB ({used:.1%})")


@typechecked
def invert_permutation(perm: pdarray) -> pdarray:
    """
    Compute the inverse of a permutation array.

    The inverse permutation undoes the effect of the original permutation.
    For a valid permutation array `perm`, this function returns an array `inv`
    such that `inv[perm[i]] == i` for all `i`.

    Parameters
    ----------
    perm : pdarray
        A permutation of the integers `[0, N-1]`, where `N` is the length of the array.

    Returns
    -------
    pdarray
        The inverse of the input permutation.

    Raises
    ------
    ValueError
        If `perm` is not a valid permutation of the range `[0, N-1]`, such as
        containing duplicates or missing values.

    Examples
    --------
    >>> import arkouda as ak
    >>> from arkouda import array, invert_permutation
    >>> perm = array([2, 0, 3, 1])
    >>> inv = invert_permutation(perm)
    >>> print(inv)
    [1 3 0 2]

    """
    unique_vals = unique(perm)
    if (not isinstance(unique_vals, pdarray)) or unique_vals.size != perm.size:
        raise ValueError("The array is not a permutation.")
    return coargsort([perm, arange(0, perm.size)])


def convert_if_categorical(values):
    """
    Convert a Categorical array to a Strings array for display purposes.

    If the input is a Categorical, it is converted to its string labels
    based on its codes. If not, the input is returned unchanged.

    Parameters
    ----------
    values : Categorical or any
        The input array, which may be a Categorical.

    Returns
    -------
    Strings or original type
        The string labels if `values` is a Categorical, otherwise the original input.

    Examples
    --------
    >>> import arkouda as ak

    Example with a Categorical
    >>> categories = ak.array(["apple", "banana", "cherry"])
    >>> cat = ak.Categorical(categories)
    >>> result = convert_if_categorical(cat)
    >>> print(result)
    ['apple', 'banana', 'cherry']

    Example with a non-Categorical input
    >>> values = ak.array([1, 2, 3])
    >>> result = convert_if_categorical(values)
    >>> print(result)
    [1 2 3]
    """
    if isinstance(values, Categorical):
        values = values.categories[values.codes]
    return values


def register(obj, name):
    """
    Register an Arkouda object with a user-specified name.

    This function registers the provided Arkouda object (`obj`) under a
    given name (`name`). It is backwards compatible with earlier versions
    of Arkouda.

    Parameters
    ----------
    obj : Arkouda object
        The Arkouda object to register.
    name : str
        The name to associate with the object.

    Returns
    -------
    Registered object
        The input object, now registered with the specified name.

    Raises
    ------
    AttributeError
        If `obj` does not have a `register` method.

    Examples
    --------
    >>> import arkouda as ak
    >>> from arkouda.util import register
    >>> obj = ak.array([1, 2, 3])
    >>> registered_obj = register(obj, "my_array")
    >>> print(registered_obj)
    [1 2 3]
    >>> registered_obj.unregister()

    Example of registering a different Arkouda object
    >>> categories = ak.array(["apple", "banana", "cherry"])
    >>> cat = ak.Categorical(categories)
    >>> registered_cat = register(cat, "my_cat")
    >>> print(registered_cat)
    ['apple', 'banana', 'cherry']
    """
    return obj.register(name)


@typechecked
def attach(name: str):
    """
    Attach a previously created Arkouda object by its registered name.

    This function retrieves an Arkouda object (e.g., `pdarray`, `DataFrame`,
    `Series`, etc.) associated with a given `name`. It returns the corresponding
    object based on the type of object stored under that name.

    Parameters
    ----------
    name : str
        The name of the object to attach.

    Returns
    -------
    object
        The Arkouda object associated with the given `name`. The returned object
        could be of any supported type, such as `pdarray`, `DataFrame`, `Series`,
        etc.

    Raises
    ------
    ValueError
        If the object type in the response message does not match any known types.

    Examples
    --------
    >>> import arkouda as ak

    Attach an existing pdarray
    >>> obj = ak.array([1, 2, 3])
    >>> registered_obj = obj.register("my_array")
    >>> arr = ak.attach("my_array")
    >>> print(arr)
    [1 2 3]
    >>> registered_obj.unregister()
    """
    from arkouda.dataframe import DataFrame
    from arkouda.index import Index, MultiIndex
    from arkouda.numpy.pdarrayclass import pdarray
    from arkouda.numpy.segarray import SegArray
    from arkouda.pandas.series import Series

    rep_msg = json.loads(cast(str, generic_msg(cmd="attach", args={"name": name})))
    rtn_obj = None
    if rep_msg["objType"].lower() == pdarray.objType.lower():
        rtn_obj = create_pdarray(rep_msg["create"])
    elif rep_msg["objType"].lower() == Strings.objType.lower():
        rtn_obj = Strings.from_return_msg(rep_msg["create"])
    elif rep_msg["objType"].lower() == Datetime.special_objType.lower():
        rtn_obj = Datetime(create_pdarray(rep_msg["create"]))
    elif rep_msg["objType"].lower() == Timedelta.special_objType.lower():
        rtn_obj = Timedelta(create_pdarray(rep_msg["create"]))
    elif rep_msg["objType"].lower() == IPv4.special_objType.lower():
        rtn_obj = IPv4(create_pdarray(rep_msg["create"]))
    elif rep_msg["objType"].lower() == SegArray.objType.lower():
        rtn_obj = SegArray.from_return_msg(rep_msg["create"])
    elif rep_msg["objType"].lower() == DataFrame.objType.lower():
        rtn_obj = DataFrame.from_return_msg(rep_msg["create"])
    elif rep_msg["objType"].lower() == GroupBy.objType.lower():
        rtn_obj = GroupBy.from_return_msg(rep_msg["create"])
    elif rep_msg["objType"].lower() == Categorical.objType.lower():
        rtn_obj = Categorical.from_return_msg(rep_msg["create"])
    elif (
        rep_msg["objType"].lower() == Index.objType.lower()
        or rep_msg["objType"].lower() == MultiIndex.objType.lower()
    ):
        rtn_obj = Index.from_return_msg(rep_msg["create"])
    elif rep_msg["objType"].lower() == Series.objType.lower():
        rtn_obj = Series.from_return_msg(rep_msg["create"])
    elif rep_msg["objType"].lower() == BitVector.special_objType.lower():
        rtn_obj = BitVector.from_return_msg(rep_msg["create"])

    if rtn_obj is not None:
        rtn_obj.registered_name = name
    return rtn_obj


@typechecked
def unregister(name: str) -> str:
    """
    Unregister an Arkouda object by its name.

    This function sends a request to unregister the Arkouda object associated
    with the specified `name`. It returns a response message indicating the
    success or failure of the operation.

    Parameters
    ----------
    name : str
        The name of the object to unregister.

    Returns
    -------
    str
        A message indicating the result of the unregister operation.

    Raises
    ------
    RuntimeError
        If the object associated with the given `name` does not exist or cannot
        be unregistered.

    Examples
    --------
    >>> import arkouda as ak

    Unregister an existing object
    >>> obj = ak.array([1, 2, 3])
    >>> registered_obj = obj.register("my_array")
    >>> response = ak.unregister("my_array")
    >>> print(response)
    Unregistered PDARRAY my_array
    """
    rep_msg = cast(str, generic_msg(cmd="unregister", args={"name": name}))

    return rep_msg


@typechecked
def is_registered(name: str, as_component: bool = False) -> bool:
    """
    Determine if the provided name is associated with a registered Arkouda object.

    This function checks if the `name` is found in the registry of objects,
    and optionally checks if it is registered as a component of a registered object.

    Parameters
    ----------
    name : str
        The name to check for in the registry.
    as_component : bool, default=False
        When True, the function checks if the name is registered as a component
        of a registered object (rather than as a standalone object).

    Returns
    -------
    bool
        `True` if the name is found in the registry, `False` otherwise.

    Raises
    ------
    KeyError
        If the registry query encounters an issue (e.g., invalid registry data or access issues).

    Examples
    --------
    >>> import arkouda as ak

    Check if a name is registered as an object
    >>> obj = ak.array([1, 2, 3])
    >>> registered_obj = obj.register("my_array")
    >>> result = ak.is_registered("my_array")
    >>> print(result)
    True
    >>> registered_obj.unregister()

    Check if a name is registered as a component
    >>> result = ak.is_registered("my_component", as_component=True)
    >>> print(result)
    False
    """
    return name in list_registry()["Components" if as_component else "Objects"]


def register_all(data: dict):
    """
    Register all objects in the provided dictionary.

    This function iterates through the dictionary `data`, registering each object
    with its corresponding name. It is useful for batch registering multiple
    objects in Arkouda.

    Parameters
    ----------
    data : dict
        A dictionary that maps the name to register the object to the object itself.
        For example, {"MyArray": ak.array([0, 1, 2])}.

    Examples
    --------
    >>> import arkouda as ak
    >>> data = { "array1": ak.array([0, 1, 2]), "array2": ak.array([3, 4, 5]) }
    >>> ak.register_all(data)

    After calling this function, "array1" and "array2" are registered
    in Arkouda, and can be accessed by their names.
    >>> ak.unregister_all(["array1", "array2"])
    """
    for reg_name, obj in data.items():
        register(obj, reg_name)


def unregister_all(names: List[str]):
    """
    Unregister all Arkouda objects associated with the provided names.

    This function iterates through the list of `names`, unregistering each
    corresponding object from the Arkouda server.

    Parameters
    ----------
    names : List of str
        A list of registered names corresponding to Arkouda objects
        that should be unregistered.

    Examples
    --------
    >>> import arkouda as ak
    >>> data = { "array1": ak.array([0, 1, 2]), "array2": ak.array([3, 4, 5]) }
    >>> ak.register_all(data)

    After calling this function, "array1" and "array2" are registered
    in Arkouda, and can be accessed by their names.
    >>> ak.unregister_all(["array1", "array2"])

    "arr1" and "arr2" are now unregistered

    """
    for n in names:
        unregister(n)


def attach_all(names: list):
    """
    Attach to all objects registered with the provided names.

    This function returns a dictionary mapping each name in the input list
    to the corresponding Arkouda object retrieved using `attach`.

    Parameters
    ----------
    names : List of str
        A list of names corresponding to registered Arkouda objects.

    Returns
    -------
    dict
        A dictionary mapping each name to the attached Arkouda object.

    Examples
    --------
    >>> import arkouda as ak
    >>> data = { "arr1": ak.array([0, 1, 2]), "arr2": ak.array([3, 4, 5]) }
    >>> ak.register_all(data)

    Assuming "arr1" and "arr2" were previously registered
    >>> attached_objs = ak.attach_all(["arr1", "arr2"])
    >>> print(attached_objs["arr1"])
    [0 1 2]
    >>> print(type(attached_objs["arr2"]))
    <class 'arkouda.numpy.pdarrayclass.pdarray'>
    >>> ak.unregister_all(["arr1", "arr2"])
    """
    return {n: attach(n) for n in names}


def sparse_sum_help(
    idx1: pdarray,
    idx2: pdarray,
    val1: pdarray,
    val2: pdarray,
    merge: bool = True,
    percent_transfer_limit: int = 100,
) -> Tuple[pdarray, pdarray]:
    """
    Sum two sparse matrices together.

    This function returns the result of summing two sparse matrices by combining
    their indices and values. Internally, it performs the equivalent of:

        ak.GroupBy(ak.concatenate([idx1, idx2])).sum(ak.concatenate((val1, val2)))

    Parameters
    ----------
    idx1 : pdarray
        Indices for the first sparse matrix.
    idx2 : pdarray
        Indices for the second sparse matrix.
    val1 : pdarray
        Values for the first sparse matrix.
    val2 : pdarray
        Values for the second sparse matrix.
    merge : bool, default=True
        If True, the indices are combined using a merge-based workflow.
        If False, a sort-based workflow is used.
    percent_transfer_limit : int, default=100
        Only used when `merge` is True. This defines the maximum percentage of
        data allowed to move between locales during the merge. If this threshold
        is exceeded, a sort-based workflow is used instead.

    Returns
    -------
    Tuple[pdarray, pdarray]
        A tuple containing:
        - The indices of the resulting sparse matrix.
        - The summed values associated with those indices.

    Examples
    --------
    >>> import arkouda as ak
    >>> idx1 = ak.array([0, 1, 3, 4, 7, 9])
    >>> idx2 = ak.array([0, 1, 3, 6, 9])
    >>> vals1 = idx1
    >>> vals2 = ak.array([10, 11, 13, 16, 19])
    >>> ak.util.sparse_sum_help(idx1, idx2, vals1, vals2)
    (array([0 1 3 4 6 7 9]), array([10 12 16 4 16 7 28]))

    >>> ak.GroupBy(ak.concatenate([idx1, idx2])).sum(ak.concatenate((vals1, vals2)))
    (array([0 1 3 4 6 7 9]), array([10 12 16 4 16 7 28]))
    """
    repMsg = generic_msg(
        cmd="sparseSumHelp",
        args={
            "idx1": idx1,
            "idx2": idx2,
            "val1": val1,
            "val2": val2,
            "merge": merge,
            "percent_transfer_limit": percent_transfer_limit,
        },
    )
    inds, vals = cast(str, repMsg).split("+", maxsplit=1)
    return create_pdarray(inds), create_pdarray(vals)


def broadcast_dims(sa: Sequence[int], sb: Sequence[int]) -> Tuple[int, ...]:
    """
    Determine the broadcasted shape of two arrays given their shapes.

    This function implements the broadcasting rules from the Array API standard
    to compute the shape resulting from broadcasting two arrays together.

    See: https://data-apis.org/array-api/latest/API_specification/broadcasting.html#algorithm

    Parameters
    ----------
    sa : Sequence[int]
        The shape of the first array.
    sb : Sequence[int]
        The shape of the second array.

    Returns
    -------
    Tuple[int, ...]
        The broadcasted shape resulting from combining `sa` and `sb`.

    Raises
    ------
    ValueError
        If the shapes are not compatible for broadcasting.

    Examples
    --------
    >>> import arkouda as ak
    >>> from arkouda.util import broadcast_dims
    >>> broadcast_dims((5, 1), (1, 3))
    (5, 3)

    >>> broadcast_dims((4,), (3, 1))
    (3, 4)
    """

    Na = len(sa)
    Nb = len(sb)
    N = max(Na, Nb)
    shapeOut = [0 for i in range(N)]

    i = N - 1
    while i >= 0:
        n1 = Na - N + i
        n2 = Nb - N + i

        d1 = sa[n1] if n1 >= 0 else 1
        d2 = sb[n2] if n2 >= 0 else 1

        if d1 == 1:
            shapeOut[i] = d2
        elif d2 == 1:
            shapeOut[i] = d1
        elif d1 == d2:
            shapeOut[i] = d1
        else:
            raise ValueError("Incompatible dimensions for broadcasting")

        i -= 1

    return tuple(shapeOut)


def convert_bytes(nbytes: int_scalars, unit: Literal["B", "KB", "MB", "GB"] = "B") -> numeric_scalars:
    """
    Convert a number of bytes to a larger unit: KB, MB, or GB.

    Parameters
    ----------
    nbytes : int_scalars
        The number of bytes to convert.
    unit : {"B", "KB", "MB", "GB"}, default="B"
        The unit to convert to. One of {"B", "KB", "MB", "GB"}.

    Returns
    -------
    numeric_scalars
        The converted value in the specified unit.

    Raises
    ------
    ValueError
        If `unit` is not one of {"B", "KB", "MB", "GB"}.

    Examples
    --------
    >>> import arkouda as ak
    >>> from arkouda.util import convert_bytes
    >>> convert_bytes(2048, unit="KB")
    2.0

    >>> convert_bytes(1048576, unit="MB")
    1.0

    >>> convert_bytes(1073741824, unit="GB")
    1.0

    """
    kb = 1024
    mb = kb * kb
    gb = mb * kb
    if unit == "B":
        return nbytes
    elif unit == "KB":
        return float(nbytes / kb)
    elif unit == "MB":
        return float(nbytes / mb)
    elif unit == "GB":
        return float(nbytes / gb)
    else:
        raise ValueError("Invalid unit. Must be one of {'B', 'KB', 'MB', 'GB'}")


def is_numeric(arry: Union[pdarray, Strings, Categorical, Series, Index]) -> builtins.bool:
    """
    Check if the dtype of the given array-like object is numeric.

    Parameters
    ----------
    arry : pdarray, Strings, Categorical, Series, or Index
        The object to check.

    Returns
    -------
    bool
        True if the dtype of `arry` is numeric, False otherwise.

    Examples
    --------
    >>> import arkouda as ak
    >>> data = ak.array([1, 2, 3, 4, 5])
    >>> ak.util.is_numeric(data)
    True

    >>> strings = ak.array(["a", "b", "c"])
    >>> ak.util.is_numeric(strings)
    False

    >>> from arkouda import Categorical
    >>> cat = Categorical(strings)
    >>> ak.util.is_numeric(cat)
    False
    """
    from arkouda.index import Index
    from arkouda.pandas.series import Series

    if isinstance(arry, (pdarray, Series, Index)):
        return _is_dtype_in_union(dtype(arry.dtype), numeric_scalars)
    else:
        return False


def is_float(arry: Union[pdarray, Strings, Categorical, Series, Index]) -> builtins.bool:
    """
    Check if the dtype of the given array-like object is a float type.

    Parameters
    ----------
    arry : pdarray, Strings, Categorical, Series, or Index
        The object to check.

    Returns
    -------
    bool
        True if the dtype of `arry` is a float type, False otherwise.

    Examples
    --------
    >>> import arkouda as ak
    >>> data = ak.array([1.0, 2, 3, 4, float('nan')])
    >>> ak.util.is_float(data)
    True

    >>> data2 = ak.arange(5)
    >>> ak.util.is_float(data2)
    False

    >>> strings = ak.array(["1.0", "2.0"])
    >>> ak.util.is_float(strings)
    False
    """
    from arkouda.index import Index
    from arkouda.pandas.series import Series

    if isinstance(arry, (pdarray, Series, Index)):
        return _is_dtype_in_union(dtype(arry.dtype), float_scalars)
    else:
        return False


def is_int(arry: Union[pdarray, Strings, Categorical, Series, Index]) -> builtins.bool:
    """
    Check if the dtype of the given array-like object is an integer type.

    Parameters
    ----------
    arry : pdarray, Strings, Categorical, Series, or Index
        The object to check.

    Returns
    -------
    bool
        True if the dtype of `arry` is an integer type, False otherwise.

    Examples
    --------
    >>> import arkouda as ak
    >>> data = ak.array([1.0, 2, 3, 4, float('nan')])
    >>> ak.util.is_int(data)
    False

    >>> data2 = ak.arange(5)
    >>> ak.util.is_int(data2)
    True

    >>> strings = ak.array(["1", "2"])
    >>> ak.util.is_int(strings)
    False
    """
    from arkouda.index import Index
    from arkouda.pandas.series import Series

    if isinstance(arry, (pdarray, Series, Index)):
        return _is_dtype_in_union(dtype(arry.dtype), int_scalars)
    else:
        return False


def map(
    values: Union[pdarray, Strings, Categorical], mapping: Union[dict, Series]
) -> Union[pdarray, Strings]:
    """
    Map the values of an array according to an input mapping.

    Parameters
    ----------
    values : pdarray, Strings, or Categorical
        The values to be mapped.
    mapping : dict or Series
        The mapping correspondence. A dictionary or Series that defines how
        to map the `values` array.

    Returns
    -------
    Union[pdarray, Strings]
        A new array with the values mapped by the provided mapping.
        The return type matches the type of `values`. If the input `Series`
        has Categorical values, the return type will be `Strings`.

    Raises
    ------
    TypeError
        If `mapping` is not of type `dict` or `Series`.
        If `values` is not of type `pdarray`, `Categorical`, or `Strings`.

    Examples
    --------
    >>> import arkouda as ak
    >>> from arkouda.numpy.util import map
    >>> a = ak.array([2, 3, 2, 3, 4])
    >>> a
    array([2 3 2 3 4])
    >>> ak.util.map(a, {4: 25.0, 2: 30.0, 1: 7.0, 3: 5.0})
    array([30.00000000000000000 5.00000000000000000 30.00000000000000000
    5.00000000000000000 25.00000000000000000])
    >>> s = ak.Series(ak.array(["a", "b", "c", "d"]), index=ak.array([4, 2, 1, 3]))
    >>> ak.util.map(a, s)
    array(['b', 'd', 'b', 'd', 'a'])
    """
    import numpy as np

    from arkouda import Series, array, broadcast, full
    from arkouda.numpy.pdarraysetops import in1d

    keys = values
    gb = GroupBy(keys, dropna=False)
    gb_keys = gb.unique_keys

    if isinstance(mapping, dict):
        mapping = Series([array(list(mapping.keys())), array(list(mapping.values()))])

    if isinstance(mapping, Series):
        xtra_keys = gb_keys[in1d(gb_keys, mapping.index.values, invert=True)]

        if xtra_keys.size > 0:
            if not isinstance(mapping.values, (Strings, Categorical)):
                nans = full(xtra_keys.size, np.nan, mapping.values.dtype)
            else:
                nans = full(xtra_keys.size, "null")

            if isinstance(xtra_keys, Categorical):
                xtra_keys = xtra_keys.to_strings()

            xtra_series = Series(nans, index=xtra_keys)
            mapping = Series.concat([mapping, xtra_series])

        if isinstance(gb_keys, Categorical):
            mapping = mapping[gb_keys.to_strings()]
        else:
            mapping = mapping[gb_keys]

        if isinstance(mapping.values, (pdarray, Strings)):
            return broadcast(gb.segments, mapping.values, permutation=gb.permutation)
        else:
            raise TypeError("Map values must be castable to pdarray or Strings.")
    else:
        raise TypeError("Map must be dict or arkouda.Series.")


def _infer_shape_from_size(size):
    """
    Infer the shape, number of dimensions (ndim), and full size from a given size or shape.

    This function is used in pdarray creation functions that allow a size (1D) or shape (multi-dim).
    If the input is a tuple, it is treated as a multidimensional shape.
    If the input is a single integer, it is treated as a 1D shape.

    Parameters
    ----------
    size : int or tuple of int
        The size (for 1D arrays) or shape (for multidimensional arrays) of the desired array.

    Returns
    -------
    tuple
        A tuple containing:
        - shape: The shape of the array (either an integer for 1D or a tuple for multidimensional).
        - ndim: The number of dimensions
        (1 for 1D, or the length of the shape tuple for multidimensional).
        - full_size: The total number of elements in the array
        (size for 1D or product of dimensions for multidimensional).

    Examples
    --------
    >>> import arkouda as ak
    >>> _infer_shape_from_size(5)
    (5, 1, 5)

    >>> _infer_shape_from_size((3, 4))
    ((3, 4), 2, 12)
    """
    # used in pdarray creation functions that allow a size (1D) or shape (multi-dim)
    shape: Union[int_scalars, Tuple[int_scalars, ...]] = 1
    if isinstance(size, tuple):
        shape = cast(Tuple, size)
        full_size = 1
        for s in cast(Tuple, shape):
            full_size *= s
        ndim = len(shape)
    else:
        full_size = cast(int, size)
        shape = full_size
        ndim = 1
    return shape, ndim, full_size


def _generate_test_shape(rank, size):
    """
    Generate a shape for a multi-dimensional array that is close to a given size,
    while ensuring each dimension is at least 2.

    The shape will consist of `rank` dimensions, where the product of the dimensions
    is close to the given `size`. The first `rank-1` dimensions are set to 2,
    and the last dimension is adjusted such that the product of all dimensions is
    close to the desired size.

    Parameters
    ----------
    rank : int
        The number of dimensions (rank) for the generated shape.
    size : int
        The desired total size of the multi-dimensional array.

    Returns
    -------
    tuple
        A tuple containing:
        - shape: The generated shape as a tuple of integers.
        - local_size: The product of the shape dimensions, i.e., the total size.

    Examples
    --------
    >>> import arkouda as ak
    >>> _generate_test_shape(3, 16)
    ((2, 2, 4), 16)

    >>> _generate_test_shape(4, 24)
    ((2, 2, 2, 3), 24)
    """
    # used to generate shapes of the form (2,2,...n) for testing multi-dim creation
    last_dim = max(2, size // (2 ** (rank - 1)))  # such that 2*2*..*n is close to size,
    shape = (rank - 1) * [2]  # and with the final dim at least 2.
    shape.append(last_dim)  # building "shape" really does take
    shape = tuple(shape)  # multiple steps because .append doesn't
    local_size = maprod(shape)  # have a return value
    return shape, local_size
