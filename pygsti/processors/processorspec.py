"""
Defines the QubitProcessorSpec class and supporting functionality.
"""
#***************************************************************************************************
# Copyright 2015, 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains certain rights
# in this software.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except
# in compliance with the License.  You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0 or in the LICENSE file in the root pyGSTi directory.
#***************************************************************************************************

import numpy as _np
import itertools as _itertools
import collections as _collections
from functools import lru_cache

from pygsti.tools import internalgates as _itgs
from pygsti.tools import symplectic as _symplectic
from pygsti.tools import optools as _ot
from pygsti.tools import basistools as _bt
from pygsti.baseobjs import qubitgraph as _qgraph
from pygsti.baseobjs.label import Label as _Lbl
from pygsti.baseobjs.nicelyserializable import NicelySerializable as _NicelySerializable
from pygsti.modelmembers.operations import OpFactory


class ProcessorSpec(_NicelySerializable):
    """
    The API presented by a quantum processor, and possible classical control processors.

    Operation names and ideal actions (e.g. gate names and their unitaries) are stored in
    a processor specification, as is the availability of the different operations and overall
    proccesor geometry.  Processor specifications do not include any information about how
    operations are parameterized or can be adjusted (at least not yet).
    """
    # base class for potentially other types of processors (not composed of just qubits)
    def __init__(self):
        pass


class QubitProcessorSpec(ProcessorSpec):
    """
    The device specification for a one or more qubit quantum computer.

    This is objected is geared towards multi-qubit devices; many of the contained
    structures are superfluous in the case of a single qubit.

    Parameters
    ----------
    num_qubits : int
        The number of qubits in the device.

    gate_names : list of strings
        The names of gates in the device.  This may include standard gate
        names known by pyGSTi (see below) or names which appear in the
        `nonstd_gate_unitaries` argument. The set of standard gate names
        includes, but is not limited to:

        - 'Gi' : the 1Q idle operation
        - 'Gx','Gy','Gz' : 1-qubit pi/2 rotations
        - 'Gxpi','Gypi','Gzpi' : 1-qubit pi rotations
        - 'Gh' : Hadamard
        - 'Gp' : phase or S-gate (i.e., ((1,0),(0,i)))
        - 'Gcphase','Gcnot','Gswap' : standard 2-qubit gates

        Alternative names can be used for all or any of these gates, but
        then they must be explicitly defined in the `nonstd_gate_unitaries`
        dictionary.  Including any standard names in `nonstd_gate_unitaries`
        overrides the default (builtin) unitary with the one supplied.

    nonstd_gate_unitaries: dictionary of numpy arrays
        A dictionary with keys that are gate names (strings) and values that are numpy arrays specifying
        quantum gates in terms of unitary matrices. This is an additional "lookup" database of unitaries -
        to add a gate to this `QubitProcessorSpec` its names still needs to appear in the `gate_names` list.
        This dictionary's values specify additional (target) native gates that can be implemented in the device
        as unitaries acting on ordinary pure-state-vectors, in the standard computationl basis. These unitaries
        need not, and often should not, be unitaries acting on all of the qubits. E.g., a CNOT gate is specified
        by a key that is the desired name for CNOT, and a value that is the standard 4 x 4 complex matrix for CNOT.
        All gate names must start with 'G'.  As an advanced behavior, a unitary-matrix-returning function which
        takes a single argument - a tuple of label arguments - may be given instead of a single matrix to create
        an operation *factory* which allows continuously-parameterized gates.  This function must also return
        an empty/dummy unitary when `None` is given as it's argument.

    availability : dict, optional
        A dictionary whose keys are some subset of the keys (which are gate names) `nonstd_gate_unitaries` and the
        strings (which are gate names) in `gate_names` and whose values are lists of qubit-label-tuples.  Each
        qubit-label-tuple must have length equal to the number of qubits the corresponding gate acts upon, and
        causes that gate to be available to act on the specified qubits. Instead of a list of tuples, values of
        `availability` may take the special values `"all-permutations"` and `"all-combinations"`, which as their
        names imply, equate to all possible permutations and combinations of the appropriate number of qubit labels
        (deterined by the gate's dimension). If a gate name is not present in `availability`, the default is
        `"all-permutations"`.  So, the availability of a gate only needs to be specified when it cannot act in every
        valid way on the qubits (e.g., the device does not have all-to-all connectivity).

    geometry : {"line","ring","grid","torus"} or QubitGraph, optional
        The type of connectivity among the qubits, specifying a graph used to
        define neighbor relationships.  Alternatively, a :class:`QubitGraph`
        object with `qubit_labels` as the node labels may be passed directly.
        This argument is only used as a convenient way of specifying gate
        availability (edge connections are used for gates whose availability
        is unspecified by `availability` or whose value there is `"all-edges"`).

    qubit_labels : list or tuple, optional
        The labels (integers or strings) of the qubits.  If `None`, then the integers starting with zero are used.

    nonstd_gate_symplecticreps : dict, optional
        A dictionary similar to `nonstd_gate_unitaries` that supplies, instead of a unitary matrix, the symplectic
        representation of a Clifford operations, given as a 2-tuple of numpy arrays.

    aux_info : dict, optional
        Any additional information that should be attached to this processor spec.
    """

    def __init__(self, num_qubits, gate_names, nonstd_gate_unitaries=None, availability=None,
                 geometry=None, qubit_labels=None, nonstd_gate_symplecticreps=None, aux_info=None):
        assert(type(num_qubits) is int), "The number of qubits, n, should be an integer!"
        if nonstd_gate_unitaries is None: nonstd_gate_unitaries = {}
        assert(not (num_qubits > 1 and availability is None and geometry is None)), \
            "For multi-qubit processors you must specify either the geometry or the availability!"

        #Store inputs for adding models later
        self.gate_names = tuple(gate_names[:])  # copy & cast to tuple
        self.nonstd_gate_unitaries = nonstd_gate_unitaries.copy() if (nonstd_gate_unitaries is not None) else {}
        #self.gate_names += list(self.nonstd_gate_unitaries.keys())  # must specify all names in `gate_names`

        # Stores the basic unitary matrices defining the gates, as it is convenient to have these easily accessable.
        self.gate_unitaries = _collections.OrderedDict()
        std_gate_unitaries = _itgs.standard_gatename_unitaries()
        for gname in gate_names:
            if gname in nonstd_gate_unitaries:
                if callable(nonstd_gate_unitaries[gname]):
                    try:
                        assert(len(nonstd_gate_unitaries[gname].shape) == 2), \
                            "Continuously parameterized gates' `shape` attribute must be a 2-tuple!"
                    except AttributeError:
                        raise ValueError("Continuously parameterized gates must have a `shape` attribute!")
                self.gate_unitaries[gname] = nonstd_gate_unitaries[gname]
                if 'idle' in gname and (availability is None or gname not in availability):
                    # apply default availability of [None] rather than all-edges to idle gates
                    availability = {} if availability is None else availability.copy()  # get a copy of the availability
                    availability[gname] = [None]  # and update availability for later processing
            elif gname in std_gate_unitaries:
                self.gate_unitaries[gname] = std_gate_unitaries[gname]
            elif 'idle' in gname:  # interpret gname as an idle gate on the given number of qubits (all by default)
                availability = {} if availability is None else availability.copy()  # get a copy of the availability
                if gname in availability:
                    assert(isinstance(availability[gname], (tuple, list))), \
                        "Inferred idle gates need a tuple-like availability!"  # (can't deal with, e.g. "all-edges" yet)
                    avail = availability[gname]
                    if len(avail) == 0: nq = num_qubits  # or should we error?
                    elif avail[0] is None: nq = num_qubits
                    else: nq = len(avail[0])
                else:
                    nq = num_qubits  # if no availability is given, assume an idle on *all* the qubits
                    availability[gname] = [None]  # and update availability for later processing
                self.gate_unitaries[gname] = nq  # an identity gate
            else:
                raise ValueError(
                    str(gname) + " is not a valid 'standard' gate name, it must be given in `nonstd_gate_unitaries`")

        # Set self.qubit_graph
        if geometry is None:
            if qubit_labels is None:
                qubit_labels = tuple(range(num_qubits))
            self.qubit_graph = _qgraph.QubitGraph(qubit_labels)  # creates a graph with no edges
        elif isinstance(geometry, _qgraph.QubitGraph):
            self.qubit_graph = geometry
            if qubit_labels is None:
                qubit_labels = self.qubit_graph.node_names
        else:  # assume geometry is a string
            if qubit_labels is None:
                qubit_labels = tuple(range(num_qubits))
            self.qubit_graph = _qgraph.QubitGraph.common_graph(num_qubits, geometry, directed=True,
                                                               qubit_labels=qubit_labels, all_directions=True)

        # If no qubit labels are provided it defaults to integers from 0 to num_qubits-1.
        if qubit_labels is None:
            self.qubit_labels = tuple(range(num_qubits))
        else:
            assert(len(qubit_labels) == num_qubits)
            self.qubit_labels = tuple(qubit_labels)

        # Set availability
        if availability is None: availability = {}
        self.availability = _collections.OrderedDict([(gatenm, availability.get(gatenm, 'all-edges'))
                                                      for gatenm in self.gate_names])
        # if _Lbl(gatenm).sslbls is not None NEEDED?

        self.compiled_from = None  # could hold (QubitProcessorSpec, compilations) tuple if not None
        self.aux_info = aux_info if (aux_info is not None) else {}  # can hold anything additional
        self._symplectic_reps = {}  # lazily-evaluated symplectic representations for Clifford gates
        if nonstd_gate_symplecticreps is not None:
            self._symplectic_reps.update(nonstd_gate_symplecticreps)
        super(QubitProcessorSpec, self).__init__()

    def _to_nice_serialization(self):
        state = super()._to_nice_serialization()

        #Note:self.nonstd_gate_unitaries can contain matrices OR callable objects OR integers
        nonstd_unitaries = {k: (obj.to_nice_serialization() if isinstance(obj, _NicelySerializable)
                                else (int(obj) if isinstance(obj, (int, _np.int64)) else self._encodemx(obj)))
                            for k, obj in self.nonstd_gate_unitaries.items()}
        state.update({'qubit_labels': list(self.qubit_labels),
                      'gate_names': list(self.gate_names),  # TODO: what if labels and not just strings?
                      'availability': self.availability,  # should just have native types
                      'geometry': self.qubit_graph.to_nice_serialization(),
                      'nonstd_gate_unitaries': nonstd_unitaries,
                      'symplectic_reps': {k: (self._encodemx(s), self._encodemx(p))
                                          for k, (s, p) in self._symplectic_reps.items()},
                      'aux_info': self.aux_info
                      })
        return state

    @classmethod
    def _from_nice_serialization(cls, state):

        def _tuplize(x):
            if isinstance(x, (list, tuple)):
                return tuple((_tuplize(el) for el in x))
            return x

        nonstd_gate_unitaries = {}
        for k, obj in state['nonstd_gate_unitaries'].items():
            if isinstance(obj, int):
                nonstd_gate_unitaries[k] = obj
            elif isinstance(obj, dict) and "module" in obj:  # then a NicelySerializable object
                nonstd_gate_unitaries[k] = _NicelySerializable.from_nice_serialization(obj)
            else:  # assume a matrix encoding of some sort (could be list or dict)
                nonstd_gate_unitaries[k] = cls._decodemx(obj)

        symplectic_reps = {k: (cls._decodemx(s), cls._decodemx(p)) for k, (s, p) in state['symplectic_reps'].items()}
        availability = {k: _tuplize(v) for k, v in state['availability'].items()}
        geometry = _qgraph.QubitGraph.from_nice_serialization(state['geometry'])

        return cls(len(state['qubit_labels']), state['gate_names'], nonstd_gate_unitaries, availability,
                   geometry, state['qubit_labels'], symplectic_reps, state['aux_info'])

    @property
    def num_qubits(self):
        """ The number of qubits. """
        return len(self.qubit_labels)

    @property
    def primitive_op_labels(self):
        """ All the primitive operation labels derived from the gate names and availabilities """
        ret = []
        for gn in self.gate_names:
            if gn.startswith('(') and gn.endswith(')'): continue  # skip implicit gate names
            avail = self.resolved_availability(gn, 'tuple')
            ret.extend([_Lbl(gn, sslbls) for sslbls in avail])
        return tuple(ret)

    def gate_num_qubits(self, gate_name):
        """
        The number of qubits that a given gate acts upon.

        Parameters
        ----------
        gate_name : str
            The name of the gate.

        Returns
        -------
        int
        """
        unitary = self.gate_unitaries[gate_name]
        if unitary is None: return self.num_qubits  # unitary=None => identity on all qubits
        if isinstance(unitary, (int, _np.int64)): return unitary  # unitary=int => identity in n qubits
        return int(round(_np.log2(unitary.shape[0])))  # possibly factory *function* SHAPE (unitary may be callable)

    def resolved_availability(self, gate_name, tuple_or_function="auto"):
        """
        The availability of a given gate, resolved as either a tuple of sslbl-tuples or a function.

        This function does more than just access the `availability` attribute, as this may
        hold special values like `"all-edges"`.  It takes the value of `self.availability[gate_name]`
        and resolves and converts it into the desired format: either a tuple of state-space labels
        or a function with a single state-space-labels-tuple argument.

        Parameters
        ----------
        gate_name : str
            The gate name to get the availability of.

        tuple_or_function : {'tuple', 'function', 'auto'}
            The type of object to return.  `'tuple'` means a tuple of state space label tuples,
            e.g. `((0,1), (1,2))`.  `'function'` means a function that takes a single state
            space label tuple argument and returns `True` or `False` to indicate whether the gate
            is available on the given target labels.  If `'auto'` is given, then either a tuple or
            function is returned - whichever is more computationally convenient.

        Returns
        -------
        tuple or function
        """
        assert(tuple_or_function in ('tuple', 'function', 'auto'))
        avail_entry = self.availability.get(gate_name, 'all-edges')
        gate_nqubits = self.gate_num_qubits(gate_name)
        return self._resolve_availability(avail_entry, gate_nqubits, tuple_or_function)

    def _resolve_availability(self, avail_entry, gate_nqubits, tuple_or_function="auto"):

        if callable(avail_entry):  # a boolean function(sslbls)
            if tuple_or_function == "tuple":
                return tuple([sslbls for sslbls in _itertools.permutations(self.qubit_labels, gate_nqubits)
                              if avail_entry(sslbls)])
            return avail_entry  # "auto" also comes here

        elif avail_entry == 'all-combinations':
            if tuple_or_function == "function":
                def _f(sslbls):
                    return set(sslbls).issubset(self.qubit_labels) and tuple(sslbls) == tuple(sorted(sslbls))
                return _f
            return tuple(_itertools.combinations(self.qubit_labels, gate_nqubits))  # "auto" also comes here

        elif avail_entry == 'all-permutations':
            if tuple_or_function == "function":
                def _f(sslbls):
                    return set(sslbls).issubset(self.qubit_labels)
                return _f
            return tuple(_itertools.permutations(self.qubit_labels, gate_nqubits))  # "auto" also comes here

        elif avail_entry == 'all-edges':
            assert(gate_nqubits in (1, 2)), \
                "I don't know how to place a %d-qubit gate on graph edges yet" % gate_nqubits
            if tuple_or_function == "function":
                def _f(sslbls):
                    if len(sslbls) == 1: return True
                    elif len(sslbls) == 2: return self.qubit_graph.is_directly_connected(sslbls[0], sslbls[1])
                    else: raise NotImplementedError()
                return _f

            # "auto" also comes here:
            if gate_nqubits == 1: return tuple([(i,) for i in self.qubit_labels])
            elif gate_nqubits == 2: return tuple(self.qubit_graph.edges(double_for_undirected=True))
            else: raise NotImplementedError()

        elif avail_entry in ('arbitrary', '*'):  # indicates user supplied factory determines allowed sslbls
            return '*'  # special signal value for this case

        else:
            if not isinstance(avail_entry, (list, tuple)):
                raise ValueError("Unrecognized availability entry: " + str(avail_entry))
            if tuple_or_function == "function":
                def _f(sslbls):
                    return sslbls in avail_entry
                return _f
            return avail_entry  # "auto" also comes here

    def is_available(self, gate_label):
        """
        Check whether a gate at a given location is available.

        Parameters
        ----------
        gate_label : Label
            The gate name and target labels to check availability of.

        Returns
        -------
        bool
        """
        if not isinstance(gate_label, _Lbl):
            gate_label = _Lbl(gate_label)
        test_fn = self.resolved_availability(gate_label.name, "function")
        if test_fn == '*':
            return True  # really should check gate factory function somehow? TODO
        else:
            return test_fn(gate_label.sslbls)

    def available_gatenames(self, sslbls):
        """
        List all the gate names that are available *within* a set of state space labels.

        This function finds all the gate names that are available for at least a
        subset of `sslbls`.

        Parameters
        ----------
        sslbls : tuple
            The state space labels to find availability within.

        Returns
        -------
        tuple of strings
            A tuple of gate names (strings).
        """
        ret = []
        for gn in self.gate_names:
            gn_nqubits = self.gate_num_qubits(gn)
            avail_fn = self.resolved_availability(gn, tuple_or_function="function")
            if gn_nqubits > len(sslbls): continue  # gate has too many qubits to fit in sslbls
            if any((avail_fn(sslbls_subset) for sslbls_subset in _itertools.permutations(sslbls, gn_nqubits))):
                ret.append(gn)
        return tuple(ret)

    def available_gatelabels(self, gate_name, sslbls):
        """
        List all the gate labels that are available for `gate_name` on at least a subset of `sslbls`.

        Parameters
        ----------
        gate_name : str
            The gate name.

        sslbls : tuple
            The state space labels to find availability within.

        Returns
        -------
        tuple of Labels
            The available gate labels (all with name `gate_name`).
        """
        gate_nqubits = self.gate_num_qubits(gate_name)
        avail_fn = self.resolved_availability(gate_name, tuple_or_function="function")
        if gate_nqubits > len(sslbls): return ()  # gate has too many qubits to fit in sslbls
        return tuple((_Lbl(gate_name, sslbls_subset) for sslbls_subset in _itertools.permutations(sslbls, gate_nqubits)
                     if avail_fn(sslbls_subset)))

    def force_recompute_gate_relationships(self):
        """
        Invalidates LRU caches for all `compute_*` methods of this object, forcing them to recompute their values.

        The `compute_*` methods of this processor spec compute various relationships and
        properties of its gates.  These routines can be computationally intensive, and so
        their values are cached for performance.  If the gates of a processor spec changes
        and its `compute_*` methods are used, `force_recompute_gate_relationships` should
        be called.
        """
        #should clear LRU cache on all @lru_cache decorated methods, which should have "compute_" prefix
        self.compute_clifford_symplectic_reps.cache_clear()
        self.compute_one_qubit_gate_relations.cache_clear()
        self.compute_multiqubit_inversion_relations.cache_clear()
        self.compute_clifford_ops_on_qubits.cache_clear()
        self.compute_ops_on_qubits.cache_clear()
        self.compute_clifford_2Q_connectivity.cache_clear()
        self.compute_2Q_connectivity.cache_clear()

    @lru_cache(maxsize=100)  # TODO: replace w/ @cached_decorator when Python 3.8+ is required, (so doesn't prevent GC)
    def compute_clifford_symplectic_reps(self, gatename_filter=None):
        """
        Constructs a dictionary of the symplectic representations for all the Clifford gates in this processor spec.

        Parameters
        ----------
        gatename_filter : iterable, optional
            A list, tuple, or set of gate names whose symplectic
            representations should be returned (if they exist).

        Returns
        -------
        dict
            keys are gate names, values are
            `(symplectic_matrix, phase_vector)` tuples.
        """
        ret = {}
        for gn, unitary in self.gate_unitaries.items():
            if gatename_filter is not None and gn not in gatename_filter: continue
            if gn not in self._symplectic_reps:
                if unitary is None:  # special case of n-qubit identity
                    unitary = _np.identity(2**self.num_qubits, 'd')  # TODO - more efficient in FUTURE
                if isinstance(unitary, (int, _np.int64)):
                    unitary = _np.identity(2**unitary, 'd')  # TODO - more efficient in FUTURE

                try:
                    self._symplectic_reps[gn] = _symplectic.unitary_to_symplectic(unitary)
                except ValueError:
                    self._symplectic_reps[gn] = None  # `gn` is not a Clifford
            if self._symplectic_reps[gn] is not None:
                ret[gn] = self._symplectic_reps[gn]
        return ret

    @lru_cache(maxsize=100)
    def compute_one_qubit_gate_relations(self):
        """
        Computes the basic pair-wise relationships relationships between the gates.

        1. It multiplies all possible combinations of two 1-qubit gates together, from
        the full model available to in this device. If the two gates multiple to
        another 1-qubit gate from this set of gates this is recorded in the dictionary
        self.oneQgate_relations. If the 1-qubit gate with name `name1` followed by the
        1-qubit gate with name `name2` multiple (up to phase) to the gate with `name3`,
        then self.oneQgate_relations[`name1`,`name2`] = `name3`.

        2. If the inverse of any 1-qubit gate is contained in the model, this is
        recorded in the dictionary self.gate_inverse.

        Returns
        -------
        gate_relations : dict
            Keys are `(gatename1, gatename2)` and values are either the gate name
            of the product of the two gates or `None`, signifying the identity.
        gate_inverses : dict
            Keys and values are gate names, mapping a gate name to its inverse
            gate (if one exists).
        """
        Id = _np.identity(4, float)
        nontrivial_gname_pauligate_pairs = []
        oneQgate_relations = {}
        gate_inverse = {}

        for gname in self.gate_names:
            U = self.gate_unitaries[gname]
            if callable(U): continue  # can't pre-process factories
            if U is None: continue  # can't pre-process global idle
            if isinstance(U, (int, _np.int64)):
                U = _np.identity(2**U, 'd')  # n-qubit identity

            # We convert to process matrices, to avoid global phase problems.
            u = _ot.unitary_to_pauligate(U)
            if u.shape == (4, 4):
                #assert(not _np.allclose(u,Id)), "Identity should *not* be included in root gate names!"
                #if _np.allclose(u, Id):
                #    _warnings.warn("The identity should often *not* be included "
                #                   "in the root gate names of a QubitProcessorSpec.")
                nontrivial_gname_pauligate_pairs.append((gname, u))

        for gname1, u1 in nontrivial_gname_pauligate_pairs:
            for gname2, u2 in nontrivial_gname_pauligate_pairs:
                ucombined = _np.dot(u2, u1)
                for gname3, u3 in nontrivial_gname_pauligate_pairs:
                    if _np.allclose(u3, ucombined):
                        # If ucombined is u3, add the gate composition relation.
                        oneQgate_relations[gname1, gname2] = gname3  # != Id (asserted above)
                    if _np.allclose(ucombined, Id):
                        # If ucombined is the identity, add the inversion relation.
                        gate_inverse[gname1] = gname2
                        gate_inverse[gname2] = gname1
                        oneQgate_relations[gname1, gname2] = None
                        # special 1Q gate relation where result is the identity (~no gates)
        return oneQgate_relations, gate_inverse

    @lru_cache(maxsize=100)
    def compute_multiqubit_inversion_relations(self):
        """
        Computes the inverses of multi-qubit (>1 qubit) gates.

        Finds whether any of the multi-qubit gates in this device also have their
        inverse in the model. That is, if the unitaries for the  multi-qubit gate with
        name `name1` followed by the multi-qubit gate (of the same dimension) with
        name `name2` multiple (up to phase) to the identity, then
        gate_inverse[`name1`] = `name2` and gate_inverse[`name2`] = `name1`

        1-qubit gates are not computed by this method, as they are be computed by the method
        :method:`compute_one_qubit_gate_relations`.

        Returns
        -------
        gate_inverse : dict
            Keys and values are gate names, mapping a gate name to its inverse
            gate (if one exists).
        """
        gate_inverse = {}
        for gname1 in self.gate_names:
            U1 = self.gate_unitaries[gname1]
            if callable(U1): continue  # can't pre-process factories
            if U1 is None: continue  # can't pre-process global idle
            if isinstance(U1, (int, _np.int64)):
                U1 = _np.identity(2**U1, 'd')  # n-qubit identity

            # We convert to process matrices, to avoid global phase problems.
            u1 = _ot.unitary_to_pauligate(U1)
            if _np.shape(u1) != (4, 4):
                for gname2 in self.gate_names:
                    U2 = self.gate_unitaries[gname2]
                    if callable(U2): continue  # can't pre-process factories
                    if U2 is None: continue  # can't pre-process global idle
                    if isinstance(U2, (int, _np.int64)):
                        U2 = _np.identity(2**U2, 'd')  # n-qubit identity

                    u2 = _ot.unitary_to_pauligate(U2)
                    if _np.shape(u2) == _np.shape(u1):
                        ucombined = _np.dot(u2, u1)
                        if _np.allclose(ucombined, _np.identity(_np.shape(u2)[0], float)):
                            gate_inverse[gname1] = gname2
                            gate_inverse[gname2] = gname1
        return gate_inverse

    ### TODO: do we still need this?
    @lru_cache(maxsize=100)
    def compute_clifford_ops_on_qubits(self):
        """
        Constructs a dictionary mapping tuples of state space labels to the clifford operations available on them.

        Returns
        -------
        dict
            A dictionary with keys that are state space label tuples and values that are lists
            of gate labels, giving the available Clifford gates on those target labels.
        """
        clifford_gates = set(self.compute_clifford_symplectic_reps().keys())
        clifford_ops_on_qubits = _collections.defaultdict(list)
        for gn in self.gate_names:
            if gn in clifford_gates:
                for sslbls in self.resolved_availability(gn, 'tuple'):
                    clifford_ops_on_qubits[sslbls].append(_Lbl(gn, sslbls))

        return clifford_ops_on_qubits

    @lru_cache(maxsize=100)
    def compute_ops_on_qubits(self):
        """
        Constructs a dictionary mapping tuples of state space labels to the operations available on them.

        Returns
        -------
        dict
            A dictionary with keys that are state space label tuples and values that are lists
            of gate labels, giving the available gates on those target labels.
        """
        ops_on_qubits = _collections.defaultdict(list)
        for gn in self.gate_names:
            #if gn in clifford_gates:
            for sslbls in self.resolved_availability(gn, 'tuple'):
                ops_on_qubits[sslbls].append(_Lbl(gn, sslbls))

        return ops_on_qubits

    ### TODO: do we still need this?
    @lru_cache(maxsize=100)
    def compute_clifford_2Q_connectivity(self):
        """
        Constructs a graph encoding the connectivity between qubits via 2-qubit Clifford gates.

        Returns
        -------
        QubitGraph
            A graph with nodes equal to the qubit labels and edges present whenever there is
            a 2-qubit Clifford gate between the vertex qubits.
        """
        # Generate clifford_qubitgraph for the multi-qubit Clifford gates. If there are multi-qubit gates
        # which are not Clifford gates then these are not counted as "connections".
        CtwoQ_connectivity = _np.zeros((self.num_qubits, self.num_qubits), dtype=bool)
        qubit_labels = self.qubit_labels
        clifford_gates = set(self.compute_clifford_symplectic_reps().keys())
        for gn in self.gate_names:
            if self.gate_num_qubits(gn) == 2 and gn in clifford_gates:
                for sslbls in self.resolved_availability(gn, 'tuple'):
                    CtwoQ_connectivity[qubit_labels.index(sslbls[0]), qubit_labels.index(sslbls[1])] = True

        return _qgraph.QubitGraph(qubit_labels, CtwoQ_connectivity)

    @lru_cache(maxsize=100)
    def compute_2Q_connectivity(self):
        """
        Constructs a graph encoding the connectivity between qubits via 2-qubit gates.

        Returns
        -------
        QubitGraph
            A graph with nodes equal to the qubit labels and edges present whenever there is
            a 2-qubit gate between the vertex qubits.
        """
        # Generate qubitgraph for the multi-qubit gates.
        twoQ_connectivity = _np.zeros((self.num_qubits, self.num_qubits), dtype=bool)
        qubit_labels = self.qubit_labels
        for gn in self.gate_names:
            if self.gate_num_qubits(gn) == 2:
                for sslbls in self.resolved_availability(gn, 'tuple'):
                    twoQ_connectivity[qubit_labels.index(sslbls[0]), qubit_labels.index(sslbls[1])] = True

        return _qgraph.QubitGraph(qubit_labels, twoQ_connectivity)

    def subset(self, gate_names_to_include='all', qubit_labels_to_keep='all'):
        """
        Construct a smaller processor specification by keeping only a select set of gates from this processor spec.

        Parameters
        ----------
        gate_names_to_include : list or tuple or set
            The gate names that should be included in the returned processor spec.

        Returns
        -------
        QubitProcessorSpec
        """
        if gate_names_to_include == 'all': gate_names_to_include = self.gate_names
        if qubit_labels_to_keep == 'all': qubit_labels_to_keep = self.qubit_labels

        gate_names = [gn for gn in gate_names_to_include if gn in self.gate_names]
        gate_unitaries = {gn: self.gate_unitaries[gn] for gn in gate_names}
        qubit_labels = [ql for ql in qubit_labels_to_keep if ql in self.qubit_labels]
        if len(qubit_labels) != len(qubit_labels_to_keep):
            raise ValueError("Some of specified qubit_labels_to_keep (%s) aren't in this procesor spec (%s)!"
                             % (str(qubit_labels_to_keep), str(self.qubit_labels)))

        def keep_avail_tuple(tup):
            if tup is None: return True  # always keep `None` availability elements
            return set(tup).issubset(qubit_labels)

        availability = {}
        for gn in gate_names:
            if isinstance(self.availability[gn], (list, tuple)):
                availability[gn] = tuple(filter(keep_avail_tuple, self.availability[gn]))
            else:
                availability[gn] = self.availability[gn]

        qubit_graph = self.qubit_graph.subgraph(qubit_labels, reset_nodes=False)

        return QubitProcessorSpec(len(qubit_labels), gate_names, gate_unitaries, availability,
                                  qubit_graph, qubit_labels)

    def map_qubit_labels(self, mapper):
        """
        Creates a new QubitProcessorSpec whose qubit labels are updated according to the mapping function `mapper`.

        Parameters
        ----------
        mapper : dict or function
            A dictionary whose keys are the existing self.qubit_labels values
            and whose value are the new labels, or a function which takes a
            single (existing qubit-label) argument and returns a new qubit label.

        Returns
        -------
        QubitProcessorSpec
        """
        def mapper_func(line_label): return mapper[line_label] \
            if isinstance(mapper, dict) else mapper(line_label)

        mapped_qubit_labels = tuple(map(mapper_func, self.qubit_labels))

        availability = {}
        for gn in self.gate_names:
            if isinstance(self.availability[gn], (list, tuple)):
                availability[gn] = tuple([(tuple(map(mapper_func, avail_el)) if (avail_el is not None) else None)
                                          for avail_el in self.availability[gn]])
                #Note: above `None` handling means that a gate with `None` in its availability (e.g. a global idle) has
                # this availability retained, meaning it remains a gate that acts on *all* the qubits, even though that
                # may be fewer than it did originally.  This is similar to how non-tuple cases work, e.g. "all-edges"
            else:
                availability[gn] = self.availability[gn]

        qubit_graph = self.qubit_graph.map_qubit_labels(mapper)

        return QubitProcessorSpec(self.num_qubits, self.gate_names, self.gate_unitaries, availability,
                                  qubit_graph, mapped_qubit_labels)

    @property
    def idle_gate_names(self):
        """The gate names that correspond to idle operations."""
        ret = []
        for gn, unitary in self.gate_unitaries.items():
            if callable(unitary) or isinstance(unitary, OpFactory): continue  # factories can't be idle gates
            #TODO: add case for (unitary is None) if this is interpreted as a global idle
            if isinstance(unitary, (int, _np.int64)) or _np.allclose(unitary, _np.identity(unitary.shape[0], 'd')):
                ret.append(gn)
        return ret

    @property
    def global_idle_gate_name(self):
        """The (first) gate name that corresponds to a global idle operation."""
        for gn in self.idle_gate_names:
            avail = self.resolved_availability(gn, 'tuple')
            if None in avail or self.qubit_labels in avail:
                return gn
        return None

    @property
    def global_idle_layer_label(self):
        """ Similar to global_idle_gate_name but include the appropriate sslbls (either `None` or all the qubits) """
        for gn in self.idle_gate_names:
            avail = self.resolved_availability(gn, 'tuple')
            if None in avail:
                return _Lbl(gn, None)
            elif self.qubit_labels in avail:
                return _Lbl(gn, self.qubit_labels)
        return None
