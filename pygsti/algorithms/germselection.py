"""
Functions for selecting a complete set of germs for a GST analysis.
"""
#***************************************************************************************************
# Copyright 2015, 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains certain rights
# in this software.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except
# in compliance with the License.  You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0 or in the LICENSE file in the root pyGSTi directory.
#***************************************************************************************************

import warnings as _warnings

import numpy as _np
import numpy.linalg as _nla
import scipy.linalg as _sla
import itertools
from math import floor

from pygsti.algorithms import grasp as _grasp
from pygsti.algorithms import scoring as _scoring
from pygsti import circuits as _circuits
from pygsti import baseobjs as _baseobjs
from pygsti.tools import mpitools as _mpit
from pygsti.baseobjs.statespace import ExplicitStateSpace as _ExplicitStateSpace
from pygsti.baseobjs.statespace import QuditSpace as _QuditSpace


FLOATSIZE = 8  # in bytes: TODO: a better way


def find_germs(target_model, randomize=True, randomization_strength=1e-2,
               num_gs_copies=5, seed=None, candidate_germ_counts=None,
               candidate_seed=None, force="singletons", algorithm='greedy',
               algorithm_kwargs=None, mem_limit=None, comm=None,
               profiler=None, verbosity=1, num_nongauge_params=None,
               num_gauge_params=None,
               assume_real=False, float_type=_np.cdouble,
               mode="all-Jac", toss_random_frac=None,
               force_rank_increase=False, save_cevd_cache_filename= None,
               load_cevd_cache_filename=None, file_compression=False):
    """
    Generate a germ set for doing GST with a given target model.

    This function provides a streamlined interface to a variety of germ
    selection algorithms. It's goal is to provide a method that typical users
    can run by simply providing a target model and leaving all other settings
    at their default values, while providing flexibility for users desiring
    more control to fine tune some of the general and algorithm-specific
    details.

    Currently, to break troublesome degeneracies and provide some confidence
    that the chosen germ set is amplificationally complete (AC) for all
    models in a neighborhood of the target model (rather than only the
    target model), an ensemble of models with random unitary perturbations
    to their gates must be provided or generated.

    Parameters
    ----------
    target_model : Model or list of Model
        The model you are aiming to implement, or a list of models that are
        copies of the model you are trying to implement (either with or
        without random unitary perturbations applied to the models).

    randomize : bool, optional
        Whether or not to add random unitary perturbations to the model(s)
        provided.

    randomization_strength : float, optional
        The size of the random unitary perturbations applied to gates in the
        model. See :meth:`~pygsti.objects.Model.randomize_with_unitary`
        for more details.

    num_gs_copies : int, optional
        The number of copies of the original model that should be used.

    seed : int, optional
        Seed for generating random unitary perturbations to models. Also
        passed along to stochastic germ-selection algorithms and to the 
        rng for dropping random fraction of germs.

    candidate_germ_counts : dict, optional
        A dictionary of *germ_length* : *count* key-value pairs, specifying
        the germ "candidate list" - a list of potential germs to draw from.
        *count* is either an integer specifying the number of random germs
        considered at the given *germ_length* or the special values `"all upto"`
        that considers all of the of all non-equivalent germs of length up to
        the corresponding *germ_length*.  If None, all germs of up to length
        6 are used, the equivalent of `{6: 'all upto'}`.

    candidate_seed : int, optional
        A seed value used when randomly selecting candidate germs.  For each
        germ length being randomly selected, the germ length is added to
        the value of `candidate_seed` to get the actual seed used.

    force : str or list, optional
        A list of Circuits which *must* be included in the final germ set.
        If set to the special string "singletons" then all length-1 strings will
        be included.  Seting to None is the same as an empty list.

    algorithm : {'greedy', 'grasp', 'slack'}, optional
        Specifies the algorithm to use to generate the germ set. Current
        options are:
        'greedy'
            Add germs one-at-a-time until the set is AC, picking the germ that
            improves the germ-set score by the largest amount at each step. See
            :func:`find_germs_breadthfirst` for more details.
        'grasp'
            Use GRASP to generate random greedy germ sets and then locally
            optimize them. See :func:`find_germs_grasp` for more
            details.
        'slack'
            From a initial set of germs, add or remove a germ at each step in
            an attempt to improve the germ-set score. Will allow moves that
            degrade the score in an attempt to escape local optima as long as
            the degredation is within some specified amount of "slack". See
            :func:`find_germs_integer_slack` for more details.

    algorithm_kwargs : dict
        Dictionary of ``{'keyword': keyword_arg}`` pairs providing keyword
        arguments for the specified `algorithm` function. See the documentation
        for functions referred to in the `algorithm` keyword documentation for
        what options are available for each algorithm.

    mem_limit : int, optional
        A rough memory limit in bytes which restricts the amount of intermediate
        values that are computed and stored.

    comm : mpi4py.MPI.Comm, optional
        When not None, an MPI communicator for distributing the computation
        across multiple processors.

    profiler : Profiler, optional
        A profiler object used for to track timing and memory usage.

    verbosity : int, optional
        The verbosity level of the :class:`~pygsti.objects.VerbosityPrinter`
        used to print log messages.

    num_nongauge_params : int, optional
        Force the number of nongauge parameters rather than rely on automated gauge optimization.
        
    float_type : numpy dtype object, optional
        Numpy data type to use for floating point arrays.
    
    toss_random_frac : float, optional
        If specified this is a number between 0 and 1 that indicates the random fraction of candidate
        germs to drop randomly following the deduping procedure.

    Returns
    -------
    list of Circuit
        A list containing the germs making up the germ set.
    """
    printer = _baseobjs.VerbosityPrinter.create_printer(verbosity, comm)
    modelList = _setup_model_list(target_model, randomize,
                                  randomization_strength, num_gs_copies, seed)
    gates = list(target_model.operations.keys())
    availableGermsList = []
    if candidate_germ_counts is None: candidate_germ_counts = {6: 'all upto'}
    for germLength, count in candidate_germ_counts.items():
        if count == "all upto":
            availableGermsList.extend(_circuits.list_all_circuits_without_powers_and_cycles(
                gates, max_length=germLength))
        else:
            seed = None if candidate_seed is None else candidate_seed + germLength
            availableGermsList.extend(_circuits.list_random_circuits_onelen(
                gates, germLength, count, seed=seed))

    printer.log('Initial Length Available Germ List: '+ str(len(availableGermsList)), 1)

    #Let's try deduping the available germ list too:
    #build a ckt cache
    ckt_cache= create_circuit_cache(target_model, availableGermsList)
    #Then dedupe this cache:
    #The second value returned is an updated ckt cache which we don't need right now
    availableGermsList, _ = clean_germ_list(target_model, ckt_cache, eq_thresh= 1e-6)
    
    printer.log('Length Available Germ List After Deduping: '+ str(len(availableGermsList)), 1)
    
    #print(availableGermsList)
    
    #If specified, drop a random fraction of the remaining candidate germs. 
    if toss_random_frac is not None:
        availableGermsList = drop_random_germs(availableGermsList, toss_random_frac, target_model, keep_bare=True, seed=seed)
    
    printer.log('Length Available Germ List After Dropping Random Fraction: '+ str(len(availableGermsList)), 1)
    
    #print(availableGermsList)
    
    #Add some checks related to the new option to switch up data types:
    if not assume_real:
        if not (float_type is _np.cdouble or float_type is _np.csingle):
            print(float_type.dtype)
            raise ValueError('Unless working with (known) real-valued quantities only, please select an appropriate complex numpy dtype (either cdouble or csingle).')
    
    #How many bytes per float?
    FLOATSIZE= float_type(0).itemsize
    
    
    dim = target_model.dim
    #Np = model_list[0].num_params #wrong:? includes spam...
    Np = target_model.num_params
    if randomize==False:
        num_gs_copies=1
    memEstimatealljac = FLOATSIZE * num_gs_copies * len(availableGermsList) * Np**2
    # for _compute_bulk_twirled_ddd
    memEstimatealljac += FLOATSIZE * num_gs_copies * len(availableGermsList) * dim**2 * Np
    # for _bulk_twirled_deriv sub-call
    printer.log("Memory estimate of %.1f GB for all-Jac mode." %
                (memEstimatealljac / 1024.0**3), 1)
                
    memEstimatesinglejac = FLOATSIZE * 3 * len(modelList) * Np**2 + \
            FLOATSIZE * 3 * len(modelList) * dim**2 * Np
    #Factor of 3 accounts for currentDDDs, testDDDs, and bestDDDs
    printer.log("Memory estimate of %.1f GB for single-Jac mode." %
                    (memEstimatesinglejac / 1024.0**3), 1)
                    
    if mem_limit is not None:
        if memEstimatealljac > mem_limit:
            printer.log("Not enough memory for all-Jac mode, mem_limit is %.1f GB." %
                    (mem_limit / 1024.0**3), 1)
            if memEstimatesinglejac > mem_limit:
                raise MemoryError("Too little memory, even for single-Jac mode!")

    if algorithm_kwargs is None:
        # Avoid danger of using empty dict for default value.
        algorithm_kwargs = {}

    if algorithm == 'greedy':
        printer.log('Using greedy algorithm.', 1)
        # Define defaults for parameters that currently have no default or
        # whose default we want to change.
        default_kwargs = {
            'germs_list': availableGermsList,
            'randomize': False,
            'seed': seed,
            'verbosity': max(0, verbosity - 1),
            'force': force,
            'op_penalty': 0.0,
            'score_func': 'all',
            'comm': comm,
            'mem_limit': mem_limit,
            'profiler': profiler,
            'num_nongauge_params': num_nongauge_params,
            'num_gauge_params': num_gauge_params,
            'float_type': float_type,
            'mode' : mode,
            'force_rank_increase': force_rank_increase,
            'save_cevd_cache_filename': save_cevd_cache_filename,
            'load_cevd_cache_filename': load_cevd_cache_filename,
            'file_compression': file_compression
        }
        for key in default_kwargs:
            if key not in algorithm_kwargs:
                algorithm_kwargs[key] = default_kwargs[key]
        germList = find_germs_breadthfirst_rev1(model_list=modelList,
                                           **algorithm_kwargs)
        if germList is not None:
            #TODO: We should already know the value of this from
            #the final output of our optimization loop, so we ought
            #to be able to avoid this function call and related overhead.
            germsetScore = compute_germ_set_score(
                germList, neighborhood=modelList,
                score_func=algorithm_kwargs['score_func'],
                op_penalty=algorithm_kwargs['op_penalty'],
                num_nongauge_params=num_nongauge_params,
                float_type=float_type)
            printer.log('Constructed germ set:', 1)
            printer.log(str([germ.str for germ in germList]), 1)
            printer.log(germsetScore, 1)
    elif algorithm == 'grasp':
        printer.log('Using GRASP algorithm.', 1)
        # Define defaults for parameters that currently have no default or
        # whose default we want to change.
        default_kwargs = {
            'alpha': 0.1,   # No real reason for setting this value of alpha.
            'germs_list': availableGermsList,
            'randomize': False,
            'seed': seed,
            'l1_penalty': 0.0,
            'op_penalty': 0.0,
            'verbosity': max(0, verbosity - 1),
            'force': force,
            'return_all': False,
            'score_func': 'all',
            'num_nongauge_params': num_nongauge_params,
            'float_type': float_type
        }
        for key in default_kwargs:
            if key not in algorithm_kwargs:
                algorithm_kwargs[key] = default_kwargs[key]
        germList = find_germs_grasp(model_list=modelList,
                                    **algorithm_kwargs)
        printer.log('Constructed germ set:', 1)

        if algorithm_kwargs['return_all'] and germList[0] is not None:
            germsetScore = compute_germ_set_score(
                germList[0], neighborhood=modelList,
                score_func=algorithm_kwargs['score_func'],
                op_penalty=algorithm_kwargs['op_penalty'],
                l1_penalty=algorithm_kwargs['l1_penalty'],
                num_nongauge_params=num_nongauge_params,
                float_type=float_type)
            printer.log(str([germ.str for germ in germList[0]]), 1)
            printer.log(germsetScore)
        elif not algorithm_kwargs['return_all'] and germList is not None:
            germsetScore = compute_germ_set_score(
                germList, neighborhood=modelList,
                score_func=algorithm_kwargs['score_func'],
                op_penalty=algorithm_kwargs['op_penalty'],
                l1_penalty=algorithm_kwargs['l1_penalty'],
                num_nongauge_params=num_nongauge_params,
                float_type=float_type)
            printer.log(str([germ.str for germ in germList]), 1)
            printer.log(germsetScore, 1)
    elif algorithm == 'slack':
        printer.log('Using slack algorithm.', 1)
        # Define defaults for parameters that currently have no default or
        # whose default we want to change.
        default_kwargs = {
            'germs_list': availableGermsList,
            'randomize': False,
            'seed': seed,
            'verbosity': max(0, verbosity - 1),
            'l1_penalty': 0.0,
            'op_penalty': 0.0,
            'force': force,
            'score_func': 'all',
            'float_type': float_type
        }
        if ('slack_frac' not in algorithm_kwargs
                and 'fixed_slack' not in algorithm_kwargs):
            algorithm_kwargs['slack_frac'] = 0.1
        for key in default_kwargs:
            if key not in algorithm_kwargs:
                algorithm_kwargs[key] = default_kwargs[key]
        germList = find_germs_integer_slack(modelList,
                                            **algorithm_kwargs)
        if germList is not None:
            germsetScore = compute_germ_set_score(
                germList, neighborhood=modelList,
                score_func=algorithm_kwargs['score_func'],
                op_penalty=algorithm_kwargs['op_penalty'],
                l1_penalty=algorithm_kwargs['l1_penalty'],
                num_nongauge_params=num_nongauge_params,
                float_type=float_type)
            printer.log('Constructed germ set:', 1)
            printer.log(str([germ.str for germ in germList]), 1)
            printer.log(germsetScore, 1)
    else:
        raise ValueError("'{}' is not a valid algorithm "
                         "identifier.".format(algorithm))

    return germList


def compute_germ_set_score(germs, target_model=None, neighborhood=None,
                           neighborhood_size=5,
                           randomization_strength=1e-2, score_func='all',
                           op_penalty=0.0, l1_penalty=0.0, num_nongauge_params=None,
                           float_type=_np.cdouble):
    """
    Calculate the score of a germ set with respect to a model.

    More precisely, this function computes the maximum score (roughly equal
    to the number of amplified parameters) for a cloud of models.
    If `target_model` is given, it serves as the center of the cloud,
    otherwise the cloud must be supplied directly via `neighborhood`.


    Parameters
    ----------
    germs : list
        The germ set

    target_model : Model, optional
        The target model, used to generate a neighborhood of randomized models.

    neighborhood : list of Models, optional
        The "cloud" of models for which scores are computed.  If not None, this
        overrides `target_model`, `neighborhood_size`, and `randomization_strength`.

    neighborhood_size : int, optional
        Number of randomized models to construct around `target_model`.

    randomization_strength : float, optional
        Strength of unitary randomizations, as passed to :method:`target_model.randomize_with_unitary`.

    score_func : {'all', 'worst'}
        Sets the objective function for scoring the eigenvalues. If 'all',
        score is ``sum(1/input_array)``. If 'worst', score is ``1/min(input_array)``.

    op_penalty : float, optional
        Coefficient for a penalty linear in the sum of the germ lengths.

    l1_penalty : float, optional
        Coefficient for a penalty linear in the number of germs.

    num_nongauge_params : int, optional
        Force the number of nongauge parameters rather than rely on automated gauge optimization.
        
    float_type : numpy dtype object, optional
        Numpy data type to use for floating point arrays.

    Returns
    -------
    CompositeScore
        The maximum score for `germs`, indicating how many parameters it amplifies.
    """
    def score_fn(x): return _scoring.list_score(x, score_func=score_func)
    if neighborhood is None:
        neighborhood = [target_model.randomize_with_unitary(randomization_strength)
                        for n in range(neighborhood_size)]
    scores = [compute_composite_germ_set_score(score_fn, model=model,
                                               partial_germs_list=germs,
                                               op_penalty=op_penalty,
                                               l1_penalty=l1_penalty,
                                               num_nongauge_params=num_nongauge_params,
                                               float_type=float_type)
              for model in neighborhood]

    return max(scores)


def _get_model_params(model_list, printer=None):
    """
    Get the number of gates and gauge parameters of the models in a list.

    Also verifies all models have the same number of gates and gauge parameters.

    Parameters
    ----------
    model_list : list of Model
        A list of models for which you want an AC germ set.

    Returns
    -------
    reducedModelList : list of Model
        The original list of models with SPAM removed
    numGaugeParams : int
        The number of non-SPAM gauge parameters for all models.
    numNonGaugeParams : int
        The number of non-SPAM non-gauge parameters for all models.
    numOps : int
        The number of gates for all models.

    Raises
    ------
    ValueError
        If the number of gauge parameters or gates varies among the models.
    """
    
    if printer is not None:
        printer.log('Calculating number of gauge and non-gauge parameters', 1)
    
    # We don't care about SPAM, since it can't be amplified.
    reducedModelList = [_remove_spam_vectors(model)
                        for model in model_list]

    # All the models should have the same number of parameters and gates, but
    # let's be paranoid here for the time being and make sure.
    numGaugeParamsList = [reducedModel.num_gauge_params
                          for reducedModel in reducedModelList]
    numGaugeParams = numGaugeParamsList[0]
    if not all([numGaugeParams == otherNumGaugeParams
                for otherNumGaugeParams in numGaugeParamsList[1:]]):
        raise ValueError("All models must have the same number of gauge "
                         "parameters!")

    numNonGaugeParamsList = [reducedModel.num_nongauge_params
                             for reducedModel in reducedModelList]
    numNonGaugeParams = numNonGaugeParamsList[0]
    if not all([numNonGaugeParams == otherNumNonGaugeParams
                for otherNumNonGaugeParams in numNonGaugeParamsList[1:]]):
        raise ValueError("All models must have the same number of non-gauge "
                         "parameters!")

    numOpsList = [len(reducedModel.operations)
                  for reducedModel in reducedModelList]
    numOps = numOpsList[0]
    if not all([numOps == otherNumOps
                for otherNumOps in numOpsList[1:]]):
        raise ValueError("All models must have the same number of gates!")

    return reducedModelList, numGaugeParams, numNonGaugeParams, numOps


def _setup_model_list(model_list, randomize, randomization_strength,
                      num_copies, seed):
    """
    Sets up a list of randomize models (helper function).
    """
    if not isinstance(model_list, (list, tuple)):
        model_list = [model_list]
    if len(model_list) > 1 and num_copies is not None:
        _warnings.warn("Ignoring num_copies={} since multiple models were "
                       "supplied.".format(num_copies))

    if randomize:
        model_list = randomize_model_list(model_list, randomization_strength,
                                          num_copies, seed)

    return model_list


def compute_composite_germ_set_score(score_fn, threshold_ac=1e6, init_n=1,
                                     partial_deriv_dagger_deriv=None, model=None,
                                     partial_germs_list=None, eps=None, germ_lengths=None,
                                     op_penalty=0.0, l1_penalty=0.0, num_nongauge_params=None,
                                     float_type=_np.cdouble):
    """
    Compute the score for a germ set when it is not AC against a model.

    Normally scores computed for germ sets against models for which they are
    not AC will simply be astronomically large. This is fine if AC is all you
    care about, but not so useful if you want to compare partial germ sets
    against one another to see which is closer to being AC. This function
    will see if the germ set is AC for the parameters corresponding to the
    largest `N` eigenvalues for increasing `N` until it finds a value of `N`
    for which the germ set is not AC or all the non gauge parameters are
    accounted for and report the value of `N` as well as the score.
    This allows partial germ set scores to be compared against one-another
    sensibly, where a larger value of `N` always beats a smaller value of `N`,
    and ties in the value of `N` are broken by the score for that value of `N`.

    Parameters
    ----------
    score_fn : callable
        A function that takes as input a list of sorted eigenvalues and returns
        a score for the partial germ set based on those eigenvalues, with lower
        scores indicating better germ sets. Usually some flavor of
        :func:`~pygsti.algorithms.scoring.list_score`.

    threshold_ac : float, optional
        Value which the score (before penalties are applied) must be lower than
        for the germ set to be considered AC.

    init_n : int
        The number of largest eigenvalues to begin with checking.

    partial_deriv_dagger_deriv : numpy.array, optional
        Array with three axes, where the first axis indexes individual germs
        within the partial germ set and the remaining axes index entries in the
        positive square of the Jacobian of each individual germ's parameters
        with respect to the model parameters.
        If this array is not supplied it will need to be computed from
        `germs_list` and `model`, which will take longer, so it is recommended
        to precompute this array if this routine will be called multiple times.

    model : Model, optional
        The model against which the germ set is to be scored. Not needed if
        `partial_deriv_dagger_deriv` is provided.

    partial_germs_list : list of Circuit, optional
        The list of germs in the partial germ set to be evaluated. Not needed
        if `partial_deriv_dagger_deriv` (and `germ_lengths` when
        ``op_penalty > 0``) are provided.

    eps : float, optional
        Used when calculating `partial_deriv_dagger_deriv` to determine if two
        eigenvalues are equal (see :func:`_bulk_twirled_deriv` for details). Not
        used if `partial_deriv_dagger_deriv` is provided.

    op_penalty : float, optional
        Coefficient for a penalty linear in the sum of the germ lengths.

    germ_lengths : numpy.array, optional
        The length of each germ. Not needed if `op_penalty` is ``0.0`` or
        `partial_germs_list` is provided.

    l1_penalty : float, optional
        Coefficient for a penalty linear in the number of germs.

    num_nongauge_params : int, optional
        Force the number of nongauge parameters rather than rely on automated gauge optimization.

    Returns
    -------
    CompositeScore
        The score for the germ set indicating how many parameters it amplifies
        and its numerical score restricted to those parameters.
    """
    if partial_deriv_dagger_deriv is None:
        if model is None or partial_germs_list is None:
            raise ValueError("Must provide either partial_deriv_dagger_deriv or "
                             "(model, partial_germs_list)!")
        else:
            pDDD_kwargs = {'model': model, 'germs_list': partial_germs_list, 'float_type':float_type}
            if eps is not None:
                pDDD_kwargs['eps'] = eps
            if germ_lengths is not None:
                pDDD_kwargs['germ_lengths'] = germ_lengths
            partial_deriv_dagger_deriv = _compute_bulk_twirled_ddd(**pDDD_kwargs)

    if num_nongauge_params is None:
        if model is None:
            raise ValueError("Must provide either num_gauge_params or model!")
        else:
            reduced_model = _remove_spam_vectors(model)
            num_nongauge_params = reduced_model.num_params - reduced_model.num_gauge_params

    # Calculate penalty scores
    numGerms = partial_deriv_dagger_deriv.shape[0]
    l1Score = l1_penalty * numGerms
    opScore = 0.0
    if op_penalty != 0.0:
        if germ_lengths is None:
            if partial_germs_list is None:
                raise ValueError("Must provide either germ_lengths or "
                                 "partial_germs_list when op_penalty != 0.0!")
            else:
                germ_lengths = _np.array([len(germ)
                                         for germ in partial_germs_list])
        opScore = op_penalty * _np.sum(germ_lengths)

    combinedDDD = _np.sum(partial_deriv_dagger_deriv, axis=0)
    sortedEigenvals = _np.sort(_np.real(_nla.eigvalsh(combinedDDD)))
    observableEigenvals = sortedEigenvals[-num_nongauge_params:]
    N_AC = 0
    AC_score = _np.inf
    for N in range(init_n, len(observableEigenvals) + 1):
        scoredEigenvals = observableEigenvals[-N:]
        candidate_AC_score = score_fn(scoredEigenvals)
        if candidate_AC_score > threshold_ac:
            break   # We've found a set of parameters for which the germ set
            # is not AC.
        else:
            AC_score = candidate_AC_score
            N_AC = N

    # OLD Apply penalties to the minor score; major part is just #amplified
    #major_score = N_AC
    #minor_score = AC_score + l1Score + opScore

    # Apply penalties to the major score
    major_score = -N_AC + opScore + l1Score
    minor_score = AC_score
    ret = _scoring.CompositeScore(major_score, minor_score, N_AC)
    #DEBUG: ret.extra = {'opScore': opScore,
    #    'sum(germ_lengths)': _np.sum(germ_lengths), 'l1': l1Score}
    return ret


def _compute_bulk_twirled_ddd(model, germs_list, eps=1e-6, check=False,
                              germ_lengths=None, comm=None, float_type=_np.cdouble):
    """
    Calculate the positive squares of the germ Jacobians.

    twirledDerivDaggerDeriv == array J.H*J contributions from each germ
    (J=Jacobian) indexed by (iGerm, iModelParam1, iModelParam2)
    size (nGerms, vec_model_dim, vec_model_dim)

    Parameters
    ----------
    model : Model
        The model defining the parameters to differentiate with respect to.

    germs_list : list
        The germ set

    eps : float, optional
        Tolerance used for testing whether two eigenvectors are degenerate
        (i.e. abs(eval1 - eval2) < eps ? )

    check : bool, optional
        Whether to perform internal consistency checks, at the expense of
        making the function slower.

    germ_lengths : numpy.ndarray, optional
        A pre-computed array of the length (depth) of each germ.

    comm : mpi4py.MPI.Comm, optional
        When not ``None``, an MPI communicator for distributing the computation
        across multiple processors.
        
    float_type : numpy dtype object, optional
        Numpy data type to use in floating point arrays.

    Returns
    -------
    twirledDerivDaggerDeriv : numpy.ndarray
        A complex array of shape `(len(germs), model.num_params, model.num_params)`.
    """
    if germ_lengths is None:
        germ_lengths = _np.array([len(germ) for germ in germs_list])

    twirledDeriv = _bulk_twirled_deriv(model, germs_list, eps, check, comm, float_type=float_type) / germ_lengths[:, None, None]

    #OLD: slow, I think because conjugate *copies* a large tensor, causing a memory bottleneck
    #twirledDerivDaggerDeriv = _np.einsum('ijk,ijl->ikl',
    #                                     _np.conjugate(twirledDeriv),
    #                                     twirledDeriv)

    #NEW: faster, one-germ-at-a-time computation requires less memory.
    nGerms, _, vec_model_dim = twirledDeriv.shape
    twirledDerivDaggerDeriv = _np.empty((nGerms, vec_model_dim, vec_model_dim),
                                        dtype=float_type)
    for i in range(nGerms):
        twirledDerivDaggerDeriv[i, :, :] = _np.dot(
            twirledDeriv[i, :, :].conjugate().T, twirledDeriv[i, :, :])

    return twirledDerivDaggerDeriv


def _compute_twirled_ddd(model, germ, eps=1e-6, float_type=_np.cdouble):
    """
    Calculate the positive squares of the germ Jacobian.

    twirledDerivDaggerDeriv == array J.H*J contributions from `germ`
    (J=Jacobian) indexed by (iModelParam1, iModelParam2)
    size (vec_model_dim, vec_model_dim)

    Parameters
    ----------
    model : Model
        The model defining the parameters to differentiate with respect to.

    germ : Circuit
        The (single) germ circuit to consider.  `J` above is the twirled
        derivative of this circuit's action (process matrix).

    eps : float, optional
        Tolerance used for testing whether two eigenvectors are degenerate
        (i.e. abs(eval1 - eval2) < eps ? )

    Returns
    -------
    numpy.ndarray
    """
    twirledDeriv = _twirled_deriv(model, germ, eps, float_type) / len(germ)
    #twirledDerivDaggerDeriv = _np.einsum('jk,jl->kl',
    #                                     _np.conjugate(twirledDeriv),
    #                                     twirledDeriv)
    twirledDerivDaggerDeriv = _np.tensordot(_np.conjugate(twirledDeriv),
                                            twirledDeriv, (0, 0))

    return twirledDerivDaggerDeriv


def _germ_set_score_slack(weights, model_num, score_func, deriv_dagger_deriv_list,
                          force_indices, force_score,
                          n_gauge_params, op_penalty, germ_lengths, l1_penalty=1e-2,
                          score_dict=None):
    """
    Returns a germ set "score" in which smaller is better.

    Also returns intentionally bad score (`force_score`) if `weights` is zero on any of
    the "forced" germs (i.e. at any index in `forcedIndices`).
    This function is included for use by :func:`find_germs_integer_slack`,
    but is not convenient for just computing the score of a germ set. For that,
    use :func:`compute_germ_set_score`.

    Parameters
    ----------
    weights : list
        The per-germ "selection weight", indicating whether the germ
        is present in the selected germ set or not.

    model_num : int
        index into `deriv_dagger_deriv_list` indicating which model (typically in
        a neighborhood) we're computing scores for.

    score_func : {'all', 'worst'}
        Sets the objective function for scoring the eigenvalues. If 'all',
        score is ``sum(1/input_array)``. If 'worst', score is ``1/min(input_array)``.

    deriv_dagger_deriv_list : numpy.ndarray
        Array of J.T * J contributions for each model.

    force_indices : list of ints
        Indices marking the germs that *must* be in the final set (or else `force_score`
        will be returned).

    force_score : float
        The score that is returned when any of the germs indexed by `force_indices` are
        not present (i.e. their weights are <= 0).

    n_gauge_params : int
        The number of gauge (not amplifiable) parameters in the model.

    op_penalty : float
        Coefficient for a penalty linear in the sum of the germ lengths.

    germ_lengths : numpy.ndarray
        A pre-computed array of the length (depth) of each germ.

    l1_penalty : float
        Coefficient for a penalty linear in the number of germs.

    score_dict : dict, optional
        A dictionary to cache the score valies for the given `model_num` and
        `weights`, i.e. `score_dict[model_num, tuple(weights)]` is set to the
        returned value.


    Returns
    -------
    float
    """
    if force_indices is not None and _np.any(weights[force_indices] <= 0):
        score = force_score
    else:
        #combinedDDD = _np.einsum('i,ijk', weights,
        #                         deriv_dagger_deriv_list[model_num])
        combinedDDD = _np.squeeze(
            _np.tensordot(_np.expand_dims(weights, 1),
                          deriv_dagger_deriv_list[model_num], (0, 0)))
        assert len(combinedDDD.shape) == 2

        sortedEigenvals = _np.sort(_np.real(_nla.eigvalsh(combinedDDD)))
        observableEigenvals = sortedEigenvals[n_gauge_params:]
        score = (_scoring.list_score(observableEigenvals, score_func)
                 + l1_penalty * _np.sum(weights)
                 + op_penalty * _np.dot(germ_lengths, weights))
    if score_dict is not None:
        # Side effect: calling _germ_set_score_slack caches result in score_dict
        score_dict[model_num, tuple(weights)] = score
    return score


def randomize_model_list(model_list, randomization_strength, num_copies,
                         seed=None):
    """
    Applies random unitary perturbations to a model or list of models.

    If `model_list` is a length-1 list, then `num_copies` determines how
    many randomizations to create.  If `model_list` containes multiple
    models, then `num_copies` must be `None` and each model is
    randomized once to create the corresponding returned model.

    Parameters
    ----------
    model_list : Model or list
        A list of Model objects.

    randomization_strength : float, optional
        Strength of unitary randomizations, as passed to :method:`Model.randomize_with_unitary`.

    num_copies : int
        The number of random perturbations of `model_list[0]` to generate when
        `len(model_list) == 1`.  A value of `None` will result in 1 copy.  If
        `len(model_list) > 1` then `num_copies` must be set to None.

    seed : int, optional
        Starting seed for randomization.  Successive randomizations receive
        successive seeds.  `None` results in random seeds.

    Returns
    -------
    list
        A list of the randomized Models.
    """
    if len(model_list) > 1 and num_copies is not None:
        raise ValueError("Input multiple models XOR request multiple "
                         "copies only!")

    newmodelList = []
    if len(model_list) > 1:
        for modelnum, model in enumerate(model_list):
            newmodelList.append(model.randomize_with_unitary(
                randomization_strength,
                seed=None if seed is None else seed + modelnum))
    else:
        for modelnum in range(num_copies if num_copies is not None else 1):
            newmodelList.append(model_list[0].randomize_with_unitary(
                randomization_strength,
                seed=None if seed is None else seed + modelnum))
    return newmodelList


def test_germs_list_completeness(model_list, germs_list, score_func, threshold, float_type=_np.cdouble):
    """
    Check to see if the germs_list is amplificationally complete (AC).

    Checks for AC with respect to all the Models in `model_list`, returning
    the index of the first Model for which it is not AC or `-1` if it is AC
    for all Models.

    Parameters
    ----------
    model_list : list
        A list of models to test.  Often this list is a neighborhood ("cloud") of
        models around a model of interest.

    germs_list : list
        A list of the germ :class:`Circuit`s (the "germ set") to test for completeness.

    score_func : {'all', 'worst'}
        Sets the objective function for scoring the eigenvalues. If 'all',
        score is ``sum(1/eigval_array)``. If 'worst', score is ``1/min(eigval_array)``.

    threshold : float, optional
        An eigenvalue of jacobian^T*jacobian is considered zero and thus a
        parameter un-amplified when its reciprocal is greater than threshold.
        Also used for eigenvector degeneracy testing in twirling operation.
        
    float_type : numpy dtype object, optional
        Numpy data type to use for floating point arrays.

    Returns
    -------
    int
        The index of the first model in `model_list` to fail the amplficational
        completeness test.
    """
    for modelNum, model in enumerate(model_list):
        initial_test = test_germ_set_infl(model, germs_list,
                                          score_func=score_func,
                                          threshold=threshold, float_type=float_type)
        if not initial_test:
            return modelNum

    # If the germs_list is complete for all models, return -1
    return -1


def _remove_spam_vectors(model):
    """
    Returns a copy of `model` with state preparations and effects removed.

    Parameters
    ----------
    model : Model
        The model to act on.

    Returns
    -------
    Model
    """
    reducedModel = model.copy()
    for prepLabel in list(reducedModel.preps.keys()):
        del reducedModel.preps[prepLabel]
    for povmLabel in list(reducedModel.povms.keys()):
        del reducedModel.povms[povmLabel]
    return reducedModel


def _num_non_spam_gauge_params(model):
    """
    Return the number of non-gauge, non-SPAM parameters in `model`.

    Equivalent to `_remove_spam_vectors(model).num_gauge_params`.

    Parameters
    ---------
    model : Model

    Parameters
    ----------
    model : Model
        The model to act on.

    Returns
    -------
    int
    """
    return _remove_spam_vectors(model).num_gauge_params


# wrt is op_dim x op_dim, so is M, Minv, Proj
# so SOP is op_dim^2 x op_dim^2 and acts on vectorized *gates*
# Recall vectorizing identity (when vec(.) concats rows as flatten does):
#     vec( A * X * B ) = A tensor B^T * vec( X )
def _super_op_for_perfect_twirl(wrt, eps, float_type=_np.cdouble):
    """Return super operator for doing a perfect twirl with respect to wrt.
    """
    assert wrt.shape[0] == wrt.shape[1]  # only square matrices allowed
    dim = wrt.shape[0]
    
    #The eigenvalues and eigenvectors of wrt can be complex valued, even for
    #real-valued transfer matrices. Need to be careful here to start off using able
    #complex data type. The actual projector onto the germs commutant appears to be strictly real valued though
    #(that makes sense because otherwise the projected derivative would become complex
    #So we should be able to cast it back to the specified float_type just before returning it.
    SuperOp = _np.zeros((dim**2, dim**2), dtype=_np.cdouble)

    # Get spectrum and eigenvectors of wrt
    wrtEvals, wrtEvecs = _np.linalg.eig(wrt)
    wrtEvecsInv = _np.linalg.inv(wrtEvecs)
    
    

    # We want to project  X -> M * (Proj_i * (Minv * X * M) * Proj_i) * Minv,
    # where M = wrtEvecs. So A = B = M * Proj_i * Minv and so
    # superop = A tensor B^T == A tensor A^T
    # NOTE: this == (A^T tensor A)^T while *Maple* germ functions seem to just
    # use A^T tensor A -> ^T difference
    for i in range(dim):
        # Create projector onto i-th eigenspace (spanned by i-th eigenvector
        # and other degenerate eigenvectors)
        Proj_i = _np.diag([(1 if (abs(wrtEvals[i] - wrtEvals[j]) <= eps)
                            else 0) for j in range(dim)])
        A = _np.dot(wrtEvecs, _np.dot(Proj_i, wrtEvecsInv))
        #if _np.linalg.norm(A.imag) > 1e-6:
        #    print("DB: imag = ",_np.linalg.norm(A.imag))
        #assert(_np.linalg.norm(A.imag) < 1e-6)
        #A = _np.real(A)
        # Need to normalize, because we are overcounting projectors onto
        # subspaces of dimension d > 1, giving us d * Proj_i tensor Proj_i^T.
        # We can fix this with a division by tr(Proj_i) = d.
        #SuperOp += _np.kron(A, A.T) / _np.trace(Proj_i)
        SuperOp += fast_kron(A, A.T) / _np.trace(Proj_i)
        # SuperOp += _np.kron(A.T,A) # Mimic Maple version (but I think this is
        # wrong... or it doesn't matter?)
    
    #Case the twirling SuperOp back to the specified float type.
    #If the float_type is a real-values one though we should probably do a quick
    #sanity check to confirm everything we're casting is actually real!
    if (float_type is _np.double) or (float_type is _np.single):
        #might as well use eps as the threshold here too.
        if _np.any(_np.imag(SuperOp)>eps):
            raise ValueError("Attempting to cast a twirling superoperator with non-trivial imaginary component to a real-valued data type.")
        #cast just the real part to specified float type.
        SuperOp=SuperOp.real.astype(float_type)
    else:
        SuperOp=SuperOp.astype(float_type)
    
    return SuperOp  # a op_dim^2 x op_dim^2 matrix


def _sq_sing_vals_from_deriv(deriv, weights=None):
    """
    Calculate the squared singular values of the Jacobian of the germ set.

    Parameters
    ----------
    deriv : numpy.array
        Array of shape ``(nGerms, flattened_op_dim, vec_model_dim)``. Each
        sub-array corresponding to an individual germ is the Jacobian of the
        vectorized gate representation of that germ raised to some power with
        respect to the model parameters, normalized by dividing by the length
        of each germ after repetition.

    weights : numpy.array
        Array of length ``nGerms``, giving the relative contributions of each
        individual germ's Jacobian to the combined Jacobian (which is calculated
        as a convex combination of the individual Jacobians).

    Returns
    -------
    numpy.array
        The sorted squared singular values of the combined Jacobian of the germ
        set.
    """
    # shape (nGerms, vec_model_dim, vec_model_dim)
    derivDaggerDeriv = _np.einsum('ijk,ijl->ikl', _np.conjugate(deriv), deriv)
    # awkward to convert to tensordot, so leave as einsum

    # Take the average of the D^dagger*D/L^2 matrices associated with each germ
    # with optional weights.
    combinedDDD = _np.average(derivDaggerDeriv, weights=weights, axis=0)
    sortedEigenvals = _np.sort(_np.real(_nla.eigvalsh(combinedDDD)))

    return sortedEigenvals


def _twirled_deriv(model, circuit, eps=1e-6, float_type=_np.cdouble):
    """
    Compute the "Twirled Derivative" of a circuit.

    The twirled derivative is obtained by acting on the standard derivative of
    a circuit with the twirling superoperator.

    Parameters
    ----------
    model : Model object
        The Model which associates operation labels with operators.

    circuit : Circuit object
        A twirled derivative of this circuit's action (process matrix) is taken.

    eps : float, optional
        Tolerance used for testing whether two eigenvectors are degenerate
        (i.e. abs(eval1 - eval2) < eps ? )
        
    float_type : numpy dtype object, optional
        Nump data type to use for floating point arrays.
        


    Returns
    -------
    numpy array
        An array of shape (op_dim^2, num_model_params)
    """
    prod = model.sim.product(circuit)

    # flattened_op_dim x vec_model_dim
    dProd = model.sim.dproduct(circuit, flat=True)

    # flattened_op_dim x flattened_op_dim
    twirler = _super_op_for_perfect_twirl(prod, eps, float_type=float_type)

    # flattened_op_dim x vec_model_dim
    return _np.dot(twirler, dProd)


def _bulk_twirled_deriv(model, circuits, eps=1e-6, check=False, comm=None, float_type=_np.cdouble):
    """
    Compute the "Twirled Derivative" of a set of circuits.

    The twirled derivative is obtained by acting on the standard derivative of
    a circuit with the twirling superoperator.

    Parameters
    ----------
    model : Model object
        The Model which associates operation labels with operators.

    circuits : list of Circuit objects
        A twirled derivative of this circuit's action (process matrix) is taken.

    eps : float, optional
        Tolerance used for testing whether two eigenvectors are degenerate
        (i.e. abs(eval1 - eval2) < eps ? )

    check : bool, optional
        Whether to perform internal consistency checks, at the expense of
        making the function slower.

    comm : mpi4py.MPI.Comm, optional
        When not None, an MPI communicator for distributing the computation
        across multiple processors.
        
    float_type : numpy dtype object, optional
        Nump data type to use for floating point arrays.

    Returns
    -------
    numpy array
        An array of shape (num_simplified_circuits, op_dim^2, num_model_params)
    """
    if len(model.preps) > 0 or len(model.povms) > 0:
        model = _remove_spam_vectors(model)
        # This function assumes model has no spam elements so `lookup` below
        #  gives indexes into products computed by evalTree.

    resource_alloc = _baseobjs.ResourceAllocation(comm=comm)
    dProds, prods = model.sim.bulk_dproduct(circuits, flat=True, return_prods=True, resource_alloc=resource_alloc)
    op_dim = model.dim
    fd = op_dim**2  # flattened gate dimension
    nCircuits = len(circuits)

    ret = _np.empty((nCircuits, fd, dProds.shape[1]), dtype=float_type)
    for i in range(nCircuits):
        # flattened_op_dim x flattened_op_dim
        twirler = _super_op_for_perfect_twirl(prods[i], eps, float_type=float_type)

        # flattened_op_dim x vec_model_dim
        ret[i] = _np.dot(twirler, dProds[i * fd:(i + 1) * fd])

    if check:
        for i, circuit in enumerate(circuits):
            chk_ret = _twirled_deriv(model, circuit, eps, float_type=float_type)
            if _nla.norm(ret[i] - chk_ret) > 1e-6:
                _warnings.warn("bulk twirled derivative norm mismatch = "
                               "%g - %g = %g"
                               % (_nla.norm(ret[i]), _nla.norm(chk_ret),
                                  _nla.norm(ret[i] - chk_ret)))  # pragma: no cover

    return ret  # nSimplifiedCircuits x flattened_op_dim x vec_model_dim


def test_germ_set_finitel(model, germs_to_test, length, weights=None,
                          return_spectrum=False, tol=1e-6):
    """
    Test whether a set of germs is able to amplify all non-gauge parameters.

    Parameters
    ----------
    model : Model
        The Model (associates operation matrices with operation labels).

    germs_to_test : list of Circuits
        List of germ circuits to test for completeness.

    length : int
        The finite length to use in amplification testing.  Larger
        values take longer to compute but give more robust results.

    weights : numpy array, optional
        A 1-D array of weights with length equal len(germs_to_test),
        which multiply the contribution of each germ to the total
        jacobian matrix determining parameter amplification. If
        None, a uniform weighting of 1.0/len(germs_to_test) is applied.

    return_spectrum : bool, optional
        If True, return the jacobian^T*jacobian spectrum in addition
        to the success flag.

    tol : float, optional
        Tolerance: an eigenvalue of jacobian^T*jacobian is considered
        zero and thus a parameter un-amplified when it is less than tol.

    Returns
    -------
    success : bool
        Whether all non-gauge parameters were amplified.
    spectrum : numpy array
        Only returned when `return_spectrum` is ``True``.  Sorted array of
        eigenvalues (from small to large) of the jacobian^T * jacobian
        matrix used to determine parameter amplification.
    """
    # Remove any SPAM vectors from model since we only want
    # to consider the set of *gate* parameters for amplification
    # and this makes sure our parameter counting is correct
    model = _remove_spam_vectors(model)

    nGerms = len(germs_to_test)
    germToPowL = [germ * length for germ in germs_to_test]

    op_dim = model.dim
    dprods = model.sim.bulk_dproduct(germToPowL, flat=True)  # shape (nGerms*flattened_op_dim, vec_model_dim)
    dprods.shape = (nGerms, op_dim**2, dprods.shape[1])

    germLengths = _np.array([len(germ) for germ in germs_to_test], 'd')

    normalizedDeriv = dprods / (length * germLengths[:, None, None])

    sortedEigenvals = _sq_sing_vals_from_deriv(normalizedDeriv, weights)

    nGaugeParams = model.num_gauge_params

    observableEigenvals = sortedEigenvals[nGaugeParams:]

    bSuccess = bool(_scoring.list_score(observableEigenvals, 'worst') < 1 / tol)

    return (bSuccess, sortedEigenvals) if return_spectrum else bSuccess


def test_germ_set_infl(model, germs_to_test, score_func='all', weights=None,
                       return_spectrum=False, threshold=1e6, check=False,
                       float_type=_np.cdouble):
    """
    Test whether a set of germs is able to amplify all non-gauge parameters.

    Parameters
    ----------
    model : Model
        The Model (associates operation matrices with operation labels).

    germs_to_test : list of Circuit
        List of germ circuits to test for completeness.

    score_func : string
        Label to indicate how a germ set is scored. See
        :func:`~pygsti.algorithms.scoring.list_score` for details.

    weights : numpy array, optional
        A 1-D array of weights with length equal len(germs_to_test),
        which multiply the contribution of each germ to the total
        jacobian matrix determining parameter amplification. If
        None, a uniform weighting of 1.0/len(germs_to_test) is applied.

    return_spectrum : bool, optional
        If ``True``, return the jacobian^T*jacobian spectrum in addition
        to the success flag.

    threshold : float, optional
        An eigenvalue of jacobian^T*jacobian is considered zero and thus a
        parameter un-amplified when its reciprocal is greater than threshold.
        Also used for eigenvector degeneracy testing in twirling operation.

    check : bool, optional
        Whether to perform internal consistency checks, at the
        expense of making the function slower.
        
    float_type: numpy dtype object, optional
        Optional numpy data type to use for internal numpy array calculations.

    Returns
    -------
    success : bool
        Whether all non-gauge parameters were amplified.
    spectrum : numpy array
        Only returned when `return_spectrum` is ``True``.  Sorted array of
        eigenvalues (from small to large) of the jacobian^T * jacobian
        matrix used to determine parameter amplification.
    """
    # Remove any SPAM vectors from model since we only want
    # to consider the set of *gate* parameters for amplification
    # and this makes sure our parameter counting is correct
    model = _remove_spam_vectors(model)

    germLengths = _np.array([len(germ) for germ in germs_to_test], _np.int64)
    twirledDerivDaggerDeriv = _compute_bulk_twirled_ddd(model, germs_to_test,
                                                        1. / threshold, check,
                                                        germLengths, 
                                                        float_type=float_type)
    # result[i] = _np.dot( twirledDeriv[i].H, twirledDeriv[i] ) i.e. matrix
    # product
    # result[i,k,l] = sum_j twirledDerivH[i,k,j] * twirledDeriv(i,j,l)
    # result[i,k,l] = sum_j twirledDeriv_conj[i,j,k] * twirledDeriv(i,j,l)

    if weights is None:
        nGerms = len(germs_to_test)
        # weights = _np.array( [1.0/nGerms]*nGerms, 'd')
        weights = _np.array([1.0] * nGerms, 'd')

    #combinedTDDD = _np.einsum('i,ijk->jk', weights, twirledDerivDaggerDeriv)
    combinedTDDD = _np.tensordot(weights, twirledDerivDaggerDeriv, (0, 0))
    sortedEigenvals = _np.sort(_np.real(_np.linalg.eigvalsh(combinedTDDD)))

    nGaugeParams = model.num_gauge_params
    observableEigenvals = sortedEigenvals[nGaugeParams:]

    bSuccess = bool(_scoring.list_score(observableEigenvals, score_func)
                    < threshold)

    return (bSuccess, sortedEigenvals) if return_spectrum else bSuccess


def find_germs_depthfirst(model_list, germs_list, randomize=True,
                          randomization_strength=1e-3, num_copies=None, seed=0, op_penalty=0,
                          score_func='all', tol=1e-6, threshold=1e6, check=False,
                          force="singletons", verbosity=0, float_type=_np.cdouble):
    """
    Greedy germ selection algorithm starting with 0 germs.

    Tries to minimize the number of germs needed to achieve amplificational
    completeness (AC). Begins with 0 germs and adds the germ that increases the
    score used to check for AC by the largest amount at each step, stopping when
    the threshold for AC is achieved.

    Parameters
    ----------
    model_list : Model or list
        The model or list of `Model`s to select germs for.

    germs_list : list of Circuit
        The list of germs to contruct a germ set from.

    randomize : bool, optional
        Whether or not to randomize `model_list` (usually just a single
        `Model`) with small (see `randomizationStrengh`) unitary maps
        in order to avoid "accidental" symmetries which could allow for
        fewer germs but *only* for that particular model.  Setting this
        to `True` will increase the run time by a factor equal to the
        numer of randomized copies (`num_copies`).

    randomization_strength : float, optional
        The strength of the unitary noise used to randomize input Model(s);
        is passed to :func:`~pygsti.objects.Model.randomize_with_unitary`.

    num_copies : int, optional
        The number of randomized models to create when only a *single* gate
        set is passed via `model_list`.  Otherwise, `num_copies` must be set
        to `None`.

    seed : int, optional
        Seed for generating random unitary perturbations to models.

    op_penalty : float, optional
        Coefficient for a penalty linear in the sum of the germ lengths.

    score_func : {'all', 'worst'}, optional
        Sets the objective function for scoring the eigenvalues. If 'all',
        score is ``sum(1/eigenvalues)``. If 'worst', score is
        ``1/min(eiganvalues)``.

    tol : float, optional
        Tolerance (`eps` arg) for :func:`_compute_bulk_twirled_ddd`, which sets
        the differece between eigenvalues below which they're treated as
        degenerate.

    threshold : float, optional
        Value which the score (before penalties are applied) must be lower than
        for a germ set to be considered AC.

    check : bool, optional
        Whether to perform internal checks (will slow down run time
        substantially).

    force : list of Circuits
        A list of `Circuit` objects which *must* be included in the final
        germ set.  If the special string "singletons" is given, then all of
        the single gates (length-1 sequences) must be included.

    verbosity : int, optional
        Level of detail printed to stdout.

    Returns
    -------
    list
        A list of the built-up germ set (a list of :class:`Circuit` objects).
    """
    printer = _baseobjs.VerbosityPrinter.create_printer(verbosity)

    model_list = _setup_model_list(model_list, randomize,
                                   randomization_strength, num_copies, seed)

    (reducedModelList,
     numGaugeParams, numNonGaugeParams, _) = _get_model_params(model_list)

    germLengths = _np.array([len(germ) for germ in germs_list], _np.int64)
    numGerms = len(germs_list)

    weights = _np.zeros(numGerms, _np.int64)
    goodGerms = []
    if force:
        if force == "singletons":
            weights[_np.where(germLengths == 1)] = 1
            goodGerms = [germ for germ
                         in _np.array(germs_list)[_np.where(germLengths == 1)]]
        else:  # force should be a list of Circuits
            for opstr in force:
                weights[germs_list.index(opstr)] = 1
            goodGerms = force[:]

    undercompleteModelNum = test_germs_list_completeness(model_list,
                                                         germs_list,
                                                         score_func,
                                                         threshold,
                                                         float_type=float_type)
    if undercompleteModelNum > -1:
        printer.warning("Complete initial germ set FAILS on model "
                        + str(undercompleteModelNum) + ". Aborting search.")
        return None

    printer.log("Complete initial germ set succeeds on all input models.", 1)
    printer.log("Now searching for best germ set.", 1)
    printer.log("Starting germ set optimization. Lower score is better.", 1)

    twirledDerivDaggerDerivList = [_compute_bulk_twirled_ddd(model, germs_list, tol,
                                                             check, germLengths, float_type=float_type)
                                   for model in model_list]

    # Dict of keyword arguments passed to compute_score_non_AC that don't
    # change from call to call
    nonAC_kwargs = {
        'score_fn': lambda x: _scoring.list_score(x, score_func=score_func),
        'threshold_ac': threshold,
        'num_nongauge_params': numNonGaugeParams,
        'op_penalty': op_penalty,
        'germ_lengths': germLengths,
        'float_type': float_type
    }

    for modelNum, reducedModel in enumerate(reducedModelList):
        derivDaggerDeriv = twirledDerivDaggerDerivList[modelNum]
        # Make sure the set of germs you come up with is AC for all
        # models.
        # Remove any SPAM vectors from model since we only want
        # to consider the set of *gate* parameters for amplification
        # and this makes sure our parameter counting is correct
        while _np.any(weights == 0):

            # As long as there are some unused germs, see if you need to add
            # another one.
            if test_germ_set_infl(reducedModel, goodGerms,
                                  score_func=score_func, threshold=threshold, float_type=float_type):
                # The germs are sufficient for the current model
                break
            candidateGerms = _np.where(weights == 0)[0]
            candidateGermScores = []
            for candidateGermIdx in _np.where(weights == 0)[0]:
                # If the germs aren't sufficient, try adding a single germ
                candidateWeights = weights.copy()
                candidateWeights[candidateGermIdx] = 1
                partialDDD = derivDaggerDeriv[
                    _np.where(candidateWeights == 1)[0], :, :]
                candidateGermScore = compute_composite_germ_set_score(
                    partial_deriv_dagger_deriv=partialDDD, **nonAC_kwargs)
                candidateGermScores.append(candidateGermScore)
            # Add the germ that give the best score
            bestCandidateGerm = candidateGerms[_np.array(
                candidateGermScores).argmin()]
            weights[bestCandidateGerm] = 1
            goodGerms.append(germs_list[bestCandidateGerm])

    return goodGerms

def find_germs_breadthfirst(model_list, germs_list, randomize=True,
                            randomization_strength=1e-3, num_copies=None, seed=0,
                            op_penalty=0, score_func='all', tol=1e-6, threshold=1e6,
                            check=False, force="singletons", pretest=True, mem_limit=None,
                            comm=None, profiler=None, verbosity=0, num_nongauge_params=None, 
                            float_type= _np.cdouble, mode="all-Jac"):
    """
    Greedy algorithm starting with 0 germs.

    Tries to minimize the number of germs needed to achieve amplificational
    completeness (AC). Begins with 0 germs and adds the germ that increases the
    score used to check for AC by the largest amount (for the model that
    currently has the lowest score) at each step, stopping when the threshold
    for AC is achieved. This strategy is something of a "breadth-first"
    approach, in contrast to :func:`find_germs_depthfirst`, which only looks at the
    scores for one model at a time until that model achieves AC, then
    turning it's attention to the remaining models.

    Parameters
    ----------
    model_list : Model or list
        The model or list of `Model`s to select germs for.

    germs_list : list of Circuit
        The list of germs to contruct a germ set from.

    randomize : bool, optional
        Whether or not to randomize `model_list` (usually just a single
        `Model`) with small (see `randomizationStrengh`) unitary maps
        in order to avoid "accidental" symmetries which could allow for
        fewer germs but *only* for that particular model.  Setting this
        to `True` will increase the run time by a factor equal to the
        numer of randomized copies (`num_copies`).

    randomization_strength : float, optional
        The strength of the unitary noise used to randomize input Model(s);
        is passed to :func:`~pygsti.objects.Model.randomize_with_unitary`.

    num_copies : int, optional
        The number of randomized models to create when only a *single* gate
        set is passed via `model_list`.  Otherwise, `num_copies` must be set
        to `None`.

    seed : int, optional
        Seed for generating random unitary perturbations to models.

    op_penalty : float, optional
        Coefficient for a penalty linear in the sum of the germ lengths.

    score_func : {'all', 'worst'}, optional
        Sets the objective function for scoring the eigenvalues. If 'all',
        score is ``sum(1/eigenvalues)``. If 'worst', score is
        ``1/min(eiganvalues)``.

    tol : float, optional
        Tolerance (`eps` arg) for :func:`_compute_bulk_twirled_ddd`, which sets
        the differece between eigenvalues below which they're treated as
        degenerate.

    threshold : float, optional
        Value which the score (before penalties are applied) must be lower than
        for a germ set to be considered AC.

    check : bool, optional
        Whether to perform internal checks (will slow down run time
        substantially).

    force : list of Circuits
        A list of `Circuit` objects which *must* be included in the final
        germ set.  If the special string "singletons" is given, then all of
        the single gates (length-1 sequences) must be included.

    pretest : boolean, optional
        Whether germ list should be initially checked for completeness.

    mem_limit : int, optional
        A rough memory limit in bytes which restricts the amount of intermediate
        values that are computed and stored.

    comm : mpi4py.MPI.Comm, optional
        When not None, an MPI communicator for distributing the computation
        across multiple processors.

    profiler : Profiler, optional
        A profiler object used for to track timing and memory usage.

    verbosity : int, optional
        Level of detail printed to stdout.

    num_nongauge_params : int, optional
        Force the number of nongauge parameters rather than rely on automated gauge optimization.
        
    float_type : numpy dtype object, optional
        Use an alternative data type for the values of the numpy arrays generated.

    Returns
    -------
    list
        A list of the built-up germ set (a list of :class:`Circuit` objects).
    """
    if comm is not None and comm.Get_size() > 1:
        from mpi4py import MPI  # not at top so pygsti doesn't require mpi4py

    printer = _baseobjs.VerbosityPrinter.create_printer(verbosity, comm)

    model_list = _setup_model_list(model_list, randomize,
                                   randomization_strength, num_copies, seed)

    dim = model_list[0].dim
    #Np = model_list[0].num_params #wrong:? includes spam...
    Np = model_list[0].num_params
    #print("DB Np = %d, Ng = %d" % (Np,Ng))
    assert(all([(mdl.dim == dim) for mdl in model_list])), \
        "All models must have the same dimension!"
    #assert(all([(mdl.num_params == Np) for mdl in model_list])), \
    #    "All models must have the same number of parameters!"

    (_, numGaugeParams,
     numNonGaugeParams, _) = _get_model_params(model_list)
    if num_nongauge_params is not None:
        numGaugeParams = numGaugeParams + numNonGaugeParams - num_nongauge_params
        numNonGaugeParams = num_nongauge_params

    germLengths = _np.array([len(germ) for germ in germs_list], _np.int64)

    numGerms = len(germs_list)

    goodGerms = []
    weights = _np.zeros(numGerms, _np.int64)
    if force:
        if force == "singletons":
            weights[_np.where(germLengths == 1)] = 1
            goodGerms = [germ for i, germ in enumerate(germs_list) if germLengths[i] == 1]
        else:  # force should be a list of Circuits
            for opstr in force:
                weights[germs_list.index(opstr)] = 1
            goodGerms = force[:]
            
    #We should do the memory estimates before the pretest:
    FLOATSIZE= float_type(0).itemsize

    memEstimatealljac = FLOATSIZE * len(model_list) * len(germs_list) * Np**2
    # for _compute_bulk_twirled_ddd
    memEstimatealljac += FLOATSIZE * len(model_list) * len(germs_list) * dim**2 * Np
    # for _bulk_twirled_deriv sub-call
    printer.log("Memory estimate of %.1f GB for all-Jac mode." %
                (memEstimatealljac / 1024.0**3), 1)            

    memEstimatesinglejac = FLOATSIZE * 3 * len(model_list) * Np**2 + \
        FLOATSIZE * 3 * len(model_list) * dim**2 * Np
    #Factor of 3 accounts for currentDDDs, testDDDs, and bestDDDs
    printer.log("Memory estimate of %.1f GB for single-Jac mode." %
                (memEstimatesinglejac / 1024.0**3), 1)            

    if mem_limit is not None:
        
        printer.log("Memory limit of %.1f GB specified." %
            (mem_limit / 1024.0**3), 1)
    
        if memEstimatesinglejac > mem_limit:
                raise MemoryError("Too little memory, even for single-Jac mode!")
    
        if mode=="all-Jac" and (memEstimatealljac > mem_limit):
            #fall back to single-Jac mode
            
            printer.log("Not enough memory for all-Jac mode, falling back to single-Jac mode.", 1)
            
            mode = "single-Jac"  # compute a single germ's jacobian at a time    

    if pretest:
        undercompleteModelNum = test_germs_list_completeness(model_list,
                                                             germs_list,
                                                             score_func,
                                                             threshold,
                                                             float_type=float_type)
        if undercompleteModelNum > -1:
            printer.warning("Complete initial germ set FAILS on model "
                            + str(undercompleteModelNum) + ".")
            printer.warning("Aborting search.")
            return None

        printer.log("Complete initial germ set succeeds on all input models.", 1)
        printer.log("Now searching for best germ set.", 1)

    printer.log("Starting germ set optimization. Lower score is better.", 1) 

    twirledDerivDaggerDerivList = None

    if mode == "all-Jac":
        twirledDerivDaggerDerivList = \
            [_compute_bulk_twirled_ddd(model, germs_list, tol,
                                       check, germLengths, comm, float_type=float_type)
             for model in model_list]
        print('Numpy Array Data Type:', twirledDerivDaggerDerivList[0].dtype)
        printer.log("Numpy array data type for twirled derivatives is: "+ str(twirledDerivDaggerDerivList[0].dtype)+
                    " If this isn't what you specified then something went wrong.", 1) 
        
        #print out some information on the rank of the J^T J matrices for each germ.
        #matrix_ranks=_np.linalg.matrix_rank(twirledDerivDaggerDerivList[0])
        #print('J^T J dimensions: ', twirledDerivDaggerDerivList[0].shape)
        #print('J^T J Ranks: ', matrix_ranks)
        #print('Rank Ratio To Num Parameters: ', matrix_ranks/twirledDerivDaggerDerivList[0].shape[1])
                    
        currentDDDList = []
        for i, derivDaggerDeriv in enumerate(twirledDerivDaggerDerivList):
            currentDDDList.append(_np.sum(derivDaggerDeriv[_np.where(weights == 1)[0], :, :], axis=0))

    elif mode == "single-Jac":
        currentDDDList = [_np.zeros((Np, Np), dtype=float_type) for mdl in model_list]

        loc_Indices, _, _ = _mpit.distribute_indices(
            list(range(len(goodGerms))), comm, False)

        with printer.progress_logging(3):
            for i, goodGermIdx in enumerate(loc_Indices):
                printer.show_progress(i, len(loc_Indices),
                                      prefix="Initial germ set computation",
                                      suffix=germs_list[goodGermIdx].str)
                #print("DB: Rank%d computing initial index %d" % (comm.Get_rank(),goodGermIdx))

                for k, model in enumerate(model_list):
                    currentDDDList[k] += _compute_twirled_ddd(
                        model, germs_list[goodGermIdx], tol, float_type=float_type)

        #aggregate each currendDDDList across all procs
        if comm is not None and comm.Get_size() > 1:
            for k, model in enumerate(model_list):
                result = _np.empty((Np, Np), dtype=float_type)
                comm.Allreduce(currentDDDList[k], result, op=MPI.SUM)
                currentDDDList[k][:, :] = result[:, :]
                result = None  # free mem
                
    elif mode== "compactEVD":
        #implement a new caching scheme which takes advantage of the fact that the J^T J matrices are typically
        #rather sparse. Instead of caching the J^T J matrices for each germ we'll cache the compact SVD of these
        #and multiply the compact SVD components through each time we need one.
        twirledDerivDaggerDerivList = \
            [_compute_bulk_twirled_ddd_compact(model, germs_list, tol,
                                              evd_tol=1e-10, float_type=float_type, printer=printer)
             for model in model_list]
             
             #_compute_bulk_twirled_ddd_compact returns a tuple with three lists
             #corresponding to the u, sigma and vh matrices for each germ's J^T J matrix's_list
             #compact svd.
        currentDDDList = []
        nonzero_weight_indices= _np.nonzero(weights)
        nonzero_weight_indices= nonzero_weight_indices[0]
        for i, derivDaggerDeriv in enumerate(twirledDerivDaggerDerivList):
            #reconstruct the needed J^T J matrices
            for j, idx in enumerate(nonzero_weight_indices):
                if j==0:
                    temp_DDD = derivDaggerDeriv[0][idx] @ derivDaggerDeriv[2][idx]
                else:
                    temp_DDD += derivDaggerDeriv[0][idx] @ derivDaggerDeriv[2][idx]
                    
                #print('temp_DDD shape= ',temp_DDD.shape) 
            currentDDDList.append(temp_DDD)

    else:  # should be unreachable since we set 'mode' internally above
        raise ValueError("Invalid mode: %s" % mode)  # pragma: no cover

    # Dict of keyword arguments passed to compute_score_non_AC that don't
    # change from call to call
    nonAC_kwargs = {
        'score_fn': lambda x: _scoring.list_score(x, score_func=score_func),
        'threshold_ac': threshold,
        'num_nongauge_params': numNonGaugeParams,
        'op_penalty': op_penalty,
        'germ_lengths': germLengths,
        'float_type': float_type
    }

    initN = 1
    while _np.any(weights == 0):
        printer.log("Outer iteration: %d of %d amplified, %d germs" %
                    (initN, numNonGaugeParams, len(goodGerms)), 2)
        # As long as there are some unused germs, see if you need to add
        # another one.
        if initN == numNonGaugeParams:
            break   # We are AC for all models, so we can stop adding germs.

        candidateGermIndices = _np.where(weights == 0)[0]
        loc_candidateIndices, owners, _ = _mpit.distribute_indices(
            candidateGermIndices, comm, False)

        # Since the germs aren't sufficient, add the best single candidate germ
        bestDDDs = None
        bestGermScore = _scoring.CompositeScore(1.0e100, 0, None)  # lower is better
        iBestCandidateGerm = None
        with printer.progress_logging(3):
            for i, candidateGermIdx in enumerate(loc_candidateIndices):
                printer.show_progress(i, len(loc_candidateIndices),
                                      prefix="Inner iter over candidate germs",
                                      suffix=germs_list[candidateGermIdx].str)

                #print("DB: Rank%d computing index %d" % (comm.Get_rank(),candidateGermIdx))
                worstScore = _scoring.CompositeScore(-1.0e100, 0, None)  # worst of all models

                # Loop over all models
                testDDDs = []
                for k, currentDDD in enumerate(currentDDDList):
                    testDDD = currentDDD.copy()

                    if mode == "all-Jac":
                        #just get cached value of deriv-dagger-deriv
                        derivDaggerDeriv = twirledDerivDaggerDerivList[k][candidateGermIdx]
                        testDDD += derivDaggerDeriv

                    elif mode == "single-Jac":
                        #compute value of deriv-dagger-deriv
                        model = model_list[k]
                        testDDD += _compute_twirled_ddd(
                            model, germs_list[candidateGermIdx], tol, float_type=float_type)
                    
                    elif mode == "compactEVD":
                        #reconstruct the J^T J matrix from it's compact SVD
                        testDDD += twirledDerivDaggerDerivList[k][0][candidateGermIdx] @ \
                                   _np.diag(twirledDerivDaggerDerivList[k][1][candidateGermIdx]) @\
                                   twirledDerivDaggerDerivList[k][2][candidateGermIdx]
                    # (else already checked above)
                    
                    nonAC_kwargs['germ_lengths'] = \
                        _np.array([len(germ) for germ in
                                   (goodGerms + [germs_list[candidateGermIdx]])])
                    worstScore = max(worstScore, compute_composite_germ_set_score(
                        partial_deriv_dagger_deriv=testDDD[None, :, :], init_n=initN,
                        **nonAC_kwargs))
                    testDDDs.append(testDDD)  # save in case this is a keeper

                # Take the score for the current germ to be its worst score
                # over all the models.
                germScore = worstScore
                printer.log(str(germScore), 4)
                if germScore < bestGermScore:
                    bestGermScore = germScore
                    iBestCandidateGerm = candidateGermIdx
                    bestDDDs = testDDDs
                testDDDs = None

        # Add the germ that gives the best germ score
        if comm is not None and comm.Get_size() > 1:
            #figure out which processor has best germ score and distribute
            # its information to the rest of the procs
            globalMinScore = comm.allreduce(bestGermScore, op=MPI.MIN)
            toSend = comm.Get_rank() if (globalMinScore == bestGermScore) \
                else comm.Get_size() + 1
            winningRank = comm.allreduce(toSend, op=MPI.MIN)
            bestGermScore = globalMinScore
            toCast = iBestCandidateGerm if (comm.Get_rank() == winningRank) else None
            iBestCandidateGerm = comm.bcast(toCast, root=winningRank)
            for k in range(len(model_list)):
                comm.Bcast(bestDDDs[k], root=winningRank)

        #Update variables for next outer iteration
        weights[iBestCandidateGerm] = 1
        initN = bestGermScore.N
        goodGerms.append(germs_list[iBestCandidateGerm])

        for k in range(len(model_list)):
            currentDDDList[k][:, :] = bestDDDs[k][:, :]
            bestDDDs[k] = None

            printer.log("Added %s to final germs (%s)" %
                        (germs_list[iBestCandidateGerm].str, str(bestGermScore)), 3)

    return goodGerms


def find_germs_integer_slack(model_list, germs_list, randomize=True,
                             randomization_strength=1e-3, num_copies=None,
                             seed=0, l1_penalty=1e-2, op_penalty=0,
                             initial_weights=None, score_func='all',
                             max_iter=100, fixed_slack=False,
                             slack_frac=False, return_all=False, tol=1e-6,
                             check=False, force="singletons",
                             force_score=1e100, threshold=1e6,
                             verbosity=1, float_type=_np.cdouble):
    """
    Find a locally optimal subset of the germs in germs_list.

    Locally optimal here means that no single germ can be excluded
    without making the smallest non-gauge eigenvalue of the
    Jacobian.H*Jacobian matrix smaller, i.e. less amplified,
    by more than a fixed or variable amount of "slack", as
    specified by `fixed_slack` or `slack_frac`.

    Parameters
    ----------
    model_list : Model or list of Model
        The list of Models to be tested.  To ensure that the returned germ
        set is amplficationally complete, it is a good idea to score potential
        germ sets against a collection (~5-10) of similar models.  The user
        may specify a single Model and a number of unitarily close copies to
        be made (set by the kwarg `num_copies`), or the user may specify their
        own list of Models, each of which in turn may or may not be
        randomized (set by the kwarg `randomize`).

    germs_list : list of Circuit
        List of all germ circuits to consider.

    randomize : Bool, optional
        Whether or not the input Model(s) are first subject to unitary
        randomization.  If ``False``, the user should perform the unitary
        randomization themselves.  Note:  If the Model(s) are perfect (e.g.
        ``std1Q_XYI.target_model()``), then the germ selection output should not be
        trusted, due to accidental degeneracies in the Model.  If the
        Model(s) include stochastic (non-unitary) error, then germ selection
        will fail, as we score amplificational completeness in the limit of
        infinite sequence length (so any stochastic noise will completely
        depolarize any sequence in that limit).  Default is ``True``.

    randomization_strength : float, optional
        The strength of the unitary noise used to randomize input Model(s);
        is passed to :func:`~pygsti.objects.Model.randomize_with_unitary`.
        Default is ``1e-3``.

    num_copies : int, optional
        The number of Model copies to be made of the input Model (prior to
        unitary randomization).  If more than one Model is passed in,
        `num_copies` should be ``None``.  If only one Model is passed in and
        `num_copies` is ``None``, no extra copies are made.

    seed : float, optional
        The starting seed used for unitary randomization.  If multiple Models
        are to be randomized, ``model_list[i]`` is randomized with ``seed +
        i``.  Default is 0.

    l1_penalty : float, optional
        How strong the penalty should be for increasing the germ set list by a
        single germ.  Default is 1e-2.

    op_penalty : float, optional
        How strong the penalty should be for increasing a germ in the germ set
        list by a single gate.  Default is 0.

    initial_weights : list-like
        List or array of either booleans or (0 or 1) integers
        specifying which germs in `germ_list` comprise the initial
        germ set.  If ``None``, then starting point includes all
        germs.

    score_func : string
        Label to indicate how a germ set is scored. See
        :func:`~pygsti.algorithms.scoring.list_score` for details.

    max_iter : int, optional
        The maximum number of iterations before giving up.

    fixed_slack : float, optional
        If not ``None``, a floating point number which specifies that excluding
        a germ is allowed to increase 1.0/smallest-non-gauge-eigenvalue by
        `fixed_slack`.  You must specify *either* `fixed_slack` or `slack_frac`.

    slack_frac : float, optional
        If not ``None``, a floating point number which specifies that excluding
        a germ is allowed to increase 1.0/smallest-non-gauge-eigenvalue by
        `fixedFrac`*100 percent.  You must specify *either* `fixed_slack` or
        `slack_frac`.

    return_all : bool, optional
        If ``True``, return the final ``weights`` vector and score dictionary
        in addition to the optimal germ list (see below).

    tol : float, optional
        Tolerance used for eigenvector degeneracy testing in twirling
        operation.

    check : bool, optional
        Whether to perform internal consistency checks, at the
        expense of making the function slower.

    force : str or list, optional
        A list of Circuits which *must* be included in the final germ set.
        If set to the special string "singletons" then all length-1 strings will
        be included.  Seting to None is the same as an empty list.

    force_score : float, optional (default is 1e100)
        When `force` designates a non-empty set of circuits, the score to
        assign any germ set that does not contain each and every required germ.

    threshold : float, optional (default is 1e6)
        Specifies a maximum score for the score matrix, above which the germ
        set is rejected as amplificationally incomplete.

    verbosity : int, optional
        Integer >= 0 indicating the amount of detail to print.

    See Also
    --------
    :class:`~pygsti.objects.Model`
    :class:`~pygsti.objects.Circuit`
    """
    printer = _baseobjs.VerbosityPrinter.create_printer(verbosity)

    model_list = _setup_model_list(model_list, randomize,
                                   randomization_strength, num_copies, seed)

    if (fixed_slack and slack_frac) or (not fixed_slack and not slack_frac):
        raise ValueError("Either fixed_slack *or* slack_frac should be specified")

    if initial_weights is not None:
        if len(germs_list) != len(initial_weights):
            raise ValueError("The lengths of germs_list (%d) and "
                             "initial_weights (%d) must match."
                             % (len(germs_list), len(initial_weights)))
        # Normalize the weights array to be 0s and 1s even if it is provided as
        # bools
        weights = _np.array([1 if x else 0 for x in initial_weights])
    else:
        weights = _np.ones(len(germs_list), _np.int64)  # default: start with all germs
#        lessWeightOnly = True # we're starting at the max-weight vector

    undercompleteModelNum = test_germs_list_completeness(model_list,
                                                         germs_list, score_func,
                                                         threshold,
                                                         float_type=float_type)
    if undercompleteModelNum > -1:
        printer.log("Complete initial germ set FAILS on model "
                    + str(undercompleteModelNum) + ".", 1)
        printer.log("Aborting search.", 1)
        return (None, None, None) if return_all else None

    printer.log("Complete initial germ set succeeds on all input models.", 1)
    printer.log("Now searching for best germ set.", 1)

    num_models = len(model_list)

    # Remove any SPAM vectors from model since we only want
    # to consider the set of *gate* parameters for amplification
    # and this makes sure our parameter counting is correct
    model0 = _remove_spam_vectors(model_list[0])

    # Initially allow adding to weight. -- maybe make this an argument??
    lessWeightOnly = False

    nGaugeParams = model0.num_gauge_params

    # score dictionary:
    #   keys = (modelNum, tuple-ized weight vector of 1's and 0's only)
    #   values = list_score
    scoreD = {}
    germLengths = _np.array([len(germ) for germ in germs_list], _np.int64)

    if force:
        if force == "singletons":
            forceIndices = _np.where(germLengths == 1)
        else:  # force should be a list of Circuits
            forceIndices = _np.array([germs_list.index(opstr) for opstr in force])
    else:
        forceIndices = None

    twirledDerivDaggerDerivList = [_compute_bulk_twirled_ddd(model, germs_list, tol, float_type=float_type)
                                   for model in model_list]

    # Dict of keyword arguments passed to _germ_set_score_slack that don't change from
    # call to call
    cs_kwargs = {
        'score_func': score_func,
        'deriv_dagger_deriv_list': twirledDerivDaggerDerivList,
        'force_indices': forceIndices,
        'force_score': force_score,
        'n_gauge_params': nGaugeParams,
        'op_penalty': op_penalty,
        'germ_lengths': germLengths,
        'l1_penalty': l1_penalty,
        'score_dict': scoreD,
    }

    scoreList = [_germ_set_score_slack(weights, model_num, **cs_kwargs)
                 for model_num in range(num_models)]
    score = _np.max(scoreList)
    L1 = sum(weights)  # ~ L1 norm of weights

    printer.log("Starting germ set optimization. Lower score is better.", 1)
    printer.log("Model has %d gauge params." % nGaugeParams, 1)

    def _get_neighbors(bool_vec):
        for i in range(len(bool_vec)):
            v = bool_vec.copy()
            v[i] = (v[i] + 1) % 2  # Toggle v[i] btwn 0 and 1
            yield v

    with printer.progress_logging(1):
        for iIter in range(max_iter):
            printer.show_progress(iIter, max_iter,
                                  suffix="score=%g, nGerms=%d" % (score, L1))

            bFoundBetterNeighbor = False
            for neighbor in _get_neighbors(weights):
                neighborScoreList = []
                for model_num in range(len(model_list)):
                    if (model_num, tuple(neighbor)) not in scoreD:
                        neighborL1 = sum(neighbor)
                        neighborScoreList.append(_germ_set_score_slack(neighbor,
                                                                       model_num,
                                                                       **cs_kwargs))
                    else:
                        neighborL1 = sum(neighbor)
                        neighborScoreList.append(scoreD[model_num,
                                                        tuple(neighbor)])

                neighborScore = _np.max(neighborScoreList)  # Take worst case.
                # Move if we've found better position; if we've relaxed, we
                # only move when L1 is improved.
                if neighborScore <= score and (neighborL1 < L1 or not lessWeightOnly):
                    weights, score, L1 = neighbor, neighborScore, neighborL1
                    bFoundBetterNeighbor = True

                    printer.log("Found better neighbor: "
                                "nGerms = %d score = %g" % (L1, score), 2)

            if not bFoundBetterNeighbor:  # Time to relax our search.
                # From now on, don't allow increasing weight L1
                lessWeightOnly = True

                if fixed_slack is False:
                    # Note score is positive (for sum of 1/lambda)
                    slack = score * slack_frac
                    # print "slack =", slack
                else:
                    slack = fixed_slack
                assert slack > 0

                printer.log("No better neighbor. Relaxing score w/slack: "
                            + "%g => %g" % (score, score + slack), 2)
                # Artificially increase score and see if any neighbor is better
                # now...
                score += slack

                for neighbor in _get_neighbors(weights):
                    scoreList = [scoreD[model_num, tuple(neighbor)]
                                 for model_num in range(len(model_list))]
                    maxScore = _np.max(scoreList)
                    if sum(neighbor) < L1 and maxScore < score:
                        weights, score, L1 = neighbor, maxScore, sum(neighbor)
                        bFoundBetterNeighbor = True
                        printer.log("Found better neighbor: "
                                    "nGerms = %d score = %g" % (L1, score), 2)

                if not bFoundBetterNeighbor:  # Relaxing didn't help!
                    printer.log("Stationary point found!", 1)
                    break  # end main for loop

            printer.log("Moving to better neighbor", 1)
            # print score
        else:
            printer.log("Hit max. iterations", 1)

    printer.log("score = %s" % score, 1)
    printer.log("weights = %s" % weights, 1)
    printer.log("L1(weights) = %s" % sum(weights), 1)

    goodGerms = []
    for index, val in enumerate(weights):
        if val == 1:
            goodGerms.append(germs_list[index])

    if return_all:
        return goodGerms, weights, scoreD
    else:
        return goodGerms


def _germ_set_score_grasp(germ_set, germs_list, twirled_deriv_dagger_deriv_list,
                          non_ac_kwargs, init_n=1):
    """
    Score a germ set against a collection of models.

    Calculate the score of the germ set with respect to each member of a
    collection of models and return the worst score among that collection.

    Parameters
    ----------
    germ_set : list of Circuit
        The set of germs to score.

    germs_list : list of Circuit
        The list of all germs whose Jacobians are provided in
        `twirled_deriv_dagger_deriv_list`.

    twirled_deriv_dagger_deriv_list : numpy.array
        Jacobians for all the germs in `germs_list` stored as a 3-dimensional
        array, where the first index indexes the particular germ.

    non_ac_kwargs : dict
        Dictionary containing further arguments to pass to
        :func:`compute_composite_germ_set_score` for the scoring of the germ set against
        individual models.

    init_n : int
        The number of eigenvalues to begin checking for amplificational
        completeness with respect to. Passed as an argument to
        :func:`compute_composite_germ_set_score`.

    Returns
    -------
    CompositeScore
        The worst score over all models of the germ set.
    """
    weights = _np.zeros(len(germs_list))
    germ_lengths = []
    for germ in germ_set:
        weights[germs_list.index(germ)] = 1
        germ_lengths.append(len(germ))
    germsVsModelScores = []
    for derivDaggerDeriv in twirled_deriv_dagger_deriv_list:
        # Loop over all models
        partialDDD = derivDaggerDeriv[_np.where(weights == 1)[0], :, :]
        kwargs = non_ac_kwargs.copy()
        if 'germ_lengths' in non_ac_kwargs:
            kwargs['germ_lengths'] = germ_lengths
        germsVsModelScores.append(compute_composite_germ_set_score(
            partial_deriv_dagger_deriv=partialDDD, init_n=init_n, **kwargs))
    # Take the score for the current germ set to be its worst score over all
    # models.
    return max(germsVsModelScores)


def find_germs_grasp(model_list, germs_list, alpha, randomize=True,
                     randomization_strength=1e-3, num_copies=None,
                     seed=None, l1_penalty=1e-2, op_penalty=0.0,
                     score_func='all', tol=1e-6, threshold=1e6,
                     check=False, force="singletons",
                     iterations=5, return_all=False, shuffle=False,
                     verbosity=0, num_nongauge_params=None, float_type=_np.cdouble):
    """
    Use GRASP to find a high-performing germ set.

    Parameters
    ----------
    model_list : Model or list of Model
        The list of Models to be tested.  To ensure that the returned germ
        set is amplficationally complete, it is a good idea to score potential
        germ sets against a collection (~5-10) of similar models.  The user
        may specify a single Model and a number of unitarily close copies to
        be made (set by the kwarg `num_copies`, or the user may specify their
        own list of Models, each of which in turn may or may not be
        randomized (set by the kwarg `randomize`).

    germs_list : list of Circuit
        List of all germ circuits to consider.

    alpha : float
        A number between 0 and 1 that roughly specifies a score theshold
        relative to the spread of scores that a germ must score better than in
        order to be included in the RCL. A value of 0 for `alpha` corresponds
        to a purely greedy algorithm (only the best-scoring germ set is
        included in the RCL), while a value of 1 for `alpha` will include all
        germs in the RCL.
        See :func:`pygsti.algorithms.scoring.filter_composite_rcl` for more details.

    randomize : Bool, optional
        Whether or not the input Model(s) are first subject to unitary
        randomization.  If ``False``, the user should perform the unitary
        randomization themselves.  Note:  If the Model(s) are perfect (e.g.
        ``std1Q_XYI.target_model()``), then the germ selection output should not be
        trusted, due to accidental degeneracies in the Model.  If the
        Model(s) include stochastic (non-unitary) error, then germ selection
        will fail, as we score amplificational completeness in the limit of
        infinite sequence length (so any stochastic noise will completely
        depolarize any sequence in that limit).

    randomization_strength : float, optional
        The strength of the unitary noise used to randomize input Model(s);
        is passed to :func:`~pygsti.objects.Model.randomize_with_unitary`.
        Default is ``1e-3``.

    num_copies : int, optional
        The number of Model copies to be made of the input Model (prior to
        unitary randomization).  If more than one Model is passed in,
        `num_copies` should be ``None``.  If only one Model is passed in and
        `num_copies` is ``None``, no extra copies are made.

    seed : float, optional
        The starting seed used for unitary randomization.  If multiple Models
        are to be randomized, ``model_list[i]`` is randomized with ``seed +
        i``.

    l1_penalty : float, optional
        How strong the penalty should be for increasing the germ set list by a
        single germ. Used for choosing between outputs of various GRASP
        iterations.

    op_penalty : float, optional
        How strong the penalty should be for increasing a germ in the germ set
        list by a single gate.

    score_func : string
        Label to indicate how a germ set is scored. See
        :func:`~pygsti.algorithms.scoring.list_score` for details.

    tol : float, optional
        Tolerance used for eigenvector degeneracy testing in twirling
        operation.

    threshold : float, optional (default is 1e6)
        Specifies a maximum score for the score matrix, above which the germ
        set is rejected as amplificationally incomplete.

    check : bool, optional
        Whether to perform internal consistency checks, at the
        expense of making the function slower.

    force : str or list, optional
        A list of Circuits which *must* be included in the final germ set.
        If set to the special string "singletons" then all length-1 strings will
        be included.  Seting to None is the same as an empty list.

    iterations : int, optional
        The number of GRASP iterations to perform.

    return_all : bool, optional
        Flag set to tell the routine if it should return lists of all
        initial constructions and local optimizations in addition to the
        optimal solution (useful for diagnostic purposes or if you're not sure
        what your `finalScoreFn` should really be).

    shuffle : bool, optional
        Whether the neighborhood should be presented to the optimizer in a
        random order (important since currently the local optimizer updates the
        solution to the first better solution it finds in the neighborhood).

    verbosity : int, optional
        Integer >= 0 indicating the amount of detail to print.

    num_nongauge_params : int, optional
        Force the number of nongauge parameters rather than rely on automated gauge optimization.
        
    float_type : Numpy dtype object, optional
        Numpy data type to use for floating point arrays

    Returns
    -------
    finalGermList : list of Circuit
        Sublist of `germs_list` specifying the final, optimal set of germs.
    """
    printer = _baseobjs.VerbosityPrinter.create_printer(verbosity)

    model_list = _setup_model_list(model_list, randomize,
                                   randomization_strength, num_copies, seed)

    (_, numGaugeParams,
     numNonGaugeParams, _) = _get_model_params(model_list)
    if num_nongauge_params is not None:
        numGaugeParams = numGaugeParams + numNonGaugeParams - num_nongauge_params
        numNonGaugeParams = num_nongauge_params

    germLengths = _np.array([len(germ) for germ in germs_list], _np.int64)

    numGerms = len(germs_list)

    initialWeights = _np.zeros(numGerms, dtype=_np.int64)
    if force:
        if force == "singletons":
            initialWeights[_np.where(germLengths == 1)] = 1
        else:  # force should be a list of Circuits
            for opstr in force:
                initialWeights[germs_list.index(opstr)] = 1

    def _get_neighbors_fn(weights): return _grasp.neighboring_weight_vectors(
        weights, forced_weights=initialWeights, shuffle=shuffle)

    undercompleteModelNum = test_germs_list_completeness(model_list,
                                                         germs_list,
                                                         score_func,
                                                         threshold,
                                                         float_type=float_type)
    if undercompleteModelNum > -1:
        printer.warning("Complete initial germ set FAILS on model "
                        + str(undercompleteModelNum) + ".")
        printer.warning("Aborting search.")
        return (None, None, None) if return_all else None

    printer.log("Complete initial germ set succeeds on all input models.", 1)
    printer.log("Now searching for best germ set.", 1)

    printer.log("Starting germ set optimization. Lower score is better.", 1)

    twirledDerivDaggerDerivList = [_compute_bulk_twirled_ddd(model, germs_list, tol,
                                                             check, germLengths, float_type=float_type)
                                   for model in model_list]

    # Dict of keyword arguments passed to compute_score_non_AC that don't
    # change from call to call
    nonAC_kwargs = {
        'score_fn': lambda x: _scoring.list_score(x, score_func=score_func),
        'threshold_ac': threshold,
        'op_penalty': op_penalty,
        'germ_lengths': germLengths,
        'num_nongauge_params': numNonGaugeParams,
        'float_type' : float_type
    }

    final_nonAC_kwargs = nonAC_kwargs.copy()
    final_nonAC_kwargs['l1_penalty'] = l1_penalty

    scoreFn = (lambda germSet:
               _germ_set_score_grasp(germSet, germs_list,
                                     twirledDerivDaggerDerivList, nonAC_kwargs,
                                     init_n=1))
    finalScoreFn = (lambda germSet:
                    _germ_set_score_grasp(germSet, germs_list,
                                          twirledDerivDaggerDerivList,
                                          final_nonAC_kwargs, init_n=1))

    #OLD: feasibleThreshold = _scoring.CompositeScore(-numNonGaugeParams,threshold,numNonGaugeParams))
    def _feasible_fn(germ_set):  # now that scoring is not ordered entirely by N
        s = _germ_set_score_grasp(germ_set, germs_list,
                                  twirledDerivDaggerDerivList, nonAC_kwargs,
                                  init_n=1)
        return (s.N >= numNonGaugeParams and s.minor < threshold)

    def rcl_fn(x): return _scoring.filter_composite_rcl(x, alpha)

    initialSolns = []
    localSolns = []

    for iteration in range(iterations):
        # This loop is parallelizable (each iteration is independent of all
        # other iterations).
        printer.log('Starting iteration {} of {}.'.format(iteration + 1,
                                                          iterations), 1)
        success = False
        failCount = 0
        while not success and failCount < 10:
            try:
                iterSolns = _grasp.run_grasp_iteration(
                    elements=germs_list, greedy_score_fn=scoreFn, rcl_fn=rcl_fn,
                    local_score_fn=scoreFn,
                    get_neighbors_fn=_get_neighbors_fn,
                    feasible_fn=_feasible_fn,
                    initial_elements=initialWeights, seed=seed,
                    verbosity=verbosity)

                initialSolns.append(iterSolns[0])
                localSolns.append(iterSolns[1])

                success = True
                printer.log('Finished iteration {} of {}.'.format(
                    iteration + 1, iterations), 1)
            except Exception as e:
                failCount += 1
                raise e if (failCount == 10) else printer.warning(e)

    finalScores = _np.array([finalScoreFn(localSoln)
                             for localSoln in localSolns])
    bestSoln = localSolns[_np.argmin(finalScores)]

    return (bestSoln, initialSolns, localSolns) if return_all else bestSoln


def clean_germ_list(model, circuit_cache, eq_thresh= 1e-6):
    #initialize an identity matrix of the appropriate dimension
    
    cleaned_circuit_cache= circuit_cache.copy()
                   
    
    #remove circuits with duplicate PTMs
    #The list of available fidcuials is typically
    #generated in such a way to be listed in increasing order
    #of depth, so if we search for dups in that order this should
    #generally favor the shorted of a pair of duplicate PTMs.
    #cleaned_cache_keys= list(cleaned_circuit_cache.keys())
    #cleaned_cache_PTMs= list(cleaned_circuit_cache.values())
    #len_cache= len(cleaned_cache_keys)
    
    #reverse the list so that the longer circuits are at the start and shorter
    #at the end for better pop behavior.
    
    #TODO: add an option to partition the list into smaller chunks to dedupe
    #separately before regrouping and deduping as a whole. Should be a good deal faster. 
    
    unseen_circs  = list(cleaned_circuit_cache.keys())
    unseen_circs.reverse()
    unique_circs  = []
    
    #While unseen_circs is not empty
    while unseen_circs:
        current_ckt = unseen_circs.pop()
        current_ckt_PTM = cleaned_circuit_cache[current_ckt]
        unique_circs.append(current_ckt)            
        #now iterate through the remaining elements of the set of unseen circuits and remove any duplicates.
        is_not_duplicate=[True]*len(unseen_circs)
        for i, ckt in enumerate(unseen_circs):
            #the default tolerance for allclose is probably fine.
            if _np.linalg.norm(cleaned_circuit_cache[ckt]-current_ckt_PTM)<eq_thresh: #use same threshold as defined in the base find_fiducials function
                is_not_duplicate[i]=False
        #reset the set of unseen circuits.
        unseen_circs=list(itertools.compress(unseen_circs, is_not_duplicate))
    
    #rebuild the circuit cache now that it has been de-duped:
    cleaned_circuit_cache_1= {ckt_key: cleaned_circuit_cache[ckt_key] for ckt_key in unique_circs}
        
    #now that we've de-duped the circuit_cache, we can pull out the keys of cleaned_circuit_cache_1 to get the
    #new list of available fiducials.
    
    cleaned_availableGermList= unique_circs
    
        
    return cleaned_availableGermList, cleaned_circuit_cache_1
    

#new function for taking a list of available fiducials and generating a cache of the PTMs
#this will also be useful trimming the list of effective identities and fiducials with
#duplicated effects.

def create_circuit_cache(model, circuit_list):
    """
    Function for generating a cache of PTMs for the available fiducials.
    
    Parameters
    ----------
    model : Model
        The model (associates operation matrices with operation labels).

    ckt_list : list of Circuits
        Full list of all fiducial circuits avalable for constructing an informationally complete state preparation.
    
    Returns
    -------
    dictionary
        A dictionary with keys given by circuits with corresponding
        entries being the PTMs for that circuit.
    
    """
    
    circuit_cache= {}
    for circuit in circuit_list:
        circuit_cache[circuit] = model.sim.product(circuit)
    
    return circuit_cache
    
#new function to drop a random fraction of the available germ list:
def drop_random_germs(candidate_list, rand_frac, target_model, keep_bare=True, seed=None):
    """
    Function for dropping a random fraction of the candidate germ list.
    
    Parameters
    ----------
    
    candidate_list : list of Circuits
        List of candidate germs
    
    target_model : Model
        The model (associates operation matrices with operation labels)
        
    rand_frac : float between 0 and 1
        random fraction of candidate germs to drop
        
    keep_bare : bool
        Whether to always include the bare germs in the returned set.
       
   
    Returns
    -------
    availableGermsList : List
        list of candidate germs with random fraction dropped.
    
    """
    
    #If keep_bare is true we should get a list of the operations
    #from the target model, then construct two lists. One of the bare
    #germs and another of the candidates sans the bare germs.
    
    
    if seed is not None:
        rng= _np.random.default_rng(seed)
    else:
        rng= _np.random.default_rng()
        
    if keep_bare:
        bare_op_labels= target_model.operations.keys()
#        #pull the labels in a different way depending on if this is a qubit or qudit state space
#        if isinstance(target_model.state_space, _ExplicitStateSpace):
#            tpb0_labels = target_model.state_space.labels[0]
#        elif isinstance(target_model.state_space, _QuditSpace):
#            tpb0_labels = target_model.state_space.qudit_labels
#        else:
#            raise ValueError('I only know how to convert the operations to their corresponding circuits for models with ExplicitStateSpace or QuditSpace associated with them')
#        bare_op_ckts= [_circuits.Circuit([op_label],line_labels=tpb0_labels) for op_label in bare_op_labels]
        bare_op_ckts= _circuits.list_all_circuits_onelen(list(bare_op_labels), length=1)
        #drop these bare ops from the candidate_list
        candidate_list= [ckt for ckt in candidate_list if ckt not in bare_op_ckts]
        
        #now sample a random fraction of these to keep:
        indices= _np.arange(len(candidate_list))
        num_to_keep= len(indices)-floor(rand_frac*len(indices))
        indices_to_keep= rng.choice(indices, size=num_to_keep, replace=False)
        
        #Now reconstruct the list of ckts from these sampled indices:
        updated_candidate_list= [candidate_list[i] for i in indices_to_keep]
        
        #add back in the bare germs
        updated_candidate_list= bare_op_ckts + updated_candidate_list
        
       
    #if not keeping the bare germs then we'll got ahead and just drop a random fraction  
    else:
        #now sample a random fraction of these to keep:
        indices= _np.arange(len(candidate_list))
        num_to_keep= len(indices)-floor(rand_frac*len(indices))
        indices_to_keep= rng.choice(indices, size=num_to_keep, replace=False)
        
        #Now reconstruct the list of ckts from these sampled indices:
        updated_candidate_list= [candidate_list[i] for i in indices_to_keep]
        
    return updated_candidate_list
        
    
#new function that computes the J^T J matrices but then returns the result in the form of the 
#compact EVD in order to save on memory.    
def _compute_bulk_twirled_ddd_compact(model, germs_list, eps,
                                       comm=None, evd_tol=1e-10,  float_type=_np.cdouble,
                                       printer=None):

    """
    Calculate the positive squares of the germ Jacobians.

    twirledDerivDaggerDeriv == array J.H*J contributions from each germ
    (J=Jacobian) indexed by (iGerm, iModelParam1, iModelParam2)
    size (nGerms, vec_model_dim, vec_model_dim)

    Parameters
    ----------
    model : Model
        The model defining the parameters to differentiate with respect to.

    germs_list : list
        The germ set

    eps : float, optional
        Tolerance used for testing whether two eigenvectors are degenerate
        (i.e. abs(eval1 - eval2) < eps ? )
        
    evd_tol : float, optional
        Tolerance used for determining if a singular value has zero magnitude when constructing the
        compact SVD.
    
    check : bool, optional
        Whether to perform internal consistency checks, at the expense of
        making the function slower.

    germ_lengths : numpy.ndarray, optional
        A pre-computed array of the length (depth) of each germ.

    comm : mpi4py.MPI.Comm, optional
        When not ``None``, an MPI communicator for distributing the computation
        across multiple processors.
        
    float_type : numpy dtype object, optional
        Numpy data type to use in floating point arrays.

    Returns
    -------
    sqrteU_list : list of numpy ndarrays
        list of the non-trivial left eigenvectors for the compact EVD for each germ
        where each left eigenvector is multiplied by the sqrt of the corresponding eigenvalue.
    e_list : ndarray
        list of non-zero eigenvalue arrays for each germ.
    """
       
    #TODO: Figure out how to pipe in a comm object to parallelize some of this with MPI.
       
    sqrteU_list=[]
    #e_list=[]
    

    
    
    if printer is not None:
        printer.log('Generating compact EVD Cache',1)
        
        with printer.progress_logging(2):
    
            for i, germ in enumerate(germs_list):
            
                printer.show_progress(iteration=i, total=len(germs_list), bar_length=25)
                    
                twirledDeriv = _twirled_deriv(model, germ, eps, float_type) / len(germ)
                #twirledDerivDaggerDeriv = _np.tensordot(_np.conjugate(twirledDeriv),
                #                                        twirledDeriv, (0, 0))
                                                        
                #now take twirledDerivDaggerDeriv and construct its compact EVD.
                #e, U= compact_EVD(twirledDerivDaggerDeriv)
                e, U= compact_EVD_via_SVD(twirledDeriv)
                
                #e_list.append(e)
                
                #by doing this I am assuming that the matrix is PSD, but since these are all
                #gramians that should be alright.
                
                #I want to use a rank-decomposition, so split the eigenvalues into a pair of diagonal
                #matrices with the square roots of the eigenvalues on the diagonal and fold those into
                #the matrix of eigenvectors by left multiplying.
                sqrteU_list.append( U@_np.diag(_np.sqrt(e)) )       
    else: 
        for i, germ in enumerate(germs_list):
                
            twirledDeriv = _twirled_deriv(model, germ, eps, float_type) / len(germ)
            #twirledDerivDaggerDeriv = _np.tensordot(_np.conjugate(twirledDeriv),
            #                                        twirledDeriv, (0, 0))
                                                    
            #now take twirledDerivDaggerDeriv and construct its compact EVD.
            #e, U= compact_EVD(twirledDerivDaggerDeriv)
            e, U= compact_EVD_via_SVD(twirledDeriv)
            
            #e_list.append(e)
            
            #by doing this I am assuming that the matrix is PSD, but since these are all
            #gramians that should be alright.
            
            #I want to use a rank-decomposition, so split the eigenvalues into a pair of diagonal
            #matrices with the square roots of the eigenvalues on the diagonal and fold those into
            #the matrix of eigenvectors by left multiplying.
            sqrteU_list.append( U@_np.diag(_np.sqrt(e)) )       
        
    return sqrteU_list#, e_list
    
#New function for computing the compact eigenvalue decompostion of a matrix.
#Assumes that we are working with a diagonalizable matrix, no safety checks made.

def compact_EVD(mat, threshold= 1e-10):
    """
    Generate the compact eigenvalue decomposition of the input matrix.
    Assumes of course that the user has specified a diagonalizable matrix,
    there are no safety checks for that made a priori.
    
    input:
    
    mat : ndarray
        input matrix we want the compact EVD for. Assumed to be diagonalizable.
        
    threshold : float, optional
        threshold value for deciding if an eigenvalue is zero.
        
    output:
    
    e : ndarray
        1-D numpy array of the non-zero eigenvalues of mat.
    U : ndarray
        Matrix such that U@diag(s)@U.conj().T=mat.
    """
    
    #take the EVD of mat.
    e, U= _np.linalg.eigh(mat)

    #How many non-zero eigenvalues are there and what are their indices
    nonzero_eigenvalue_indices= _np.nonzero(_np.abs(e)>threshold)

    #extract the corresponding columns and values fom U and s:
    #For EVD/eigh We want the columns of U and the rows of Uh:
    nonzero_e_values = e[nonzero_eigenvalue_indices]
    nonzero_U_columns = U[:, nonzero_eigenvalue_indices[0]]
    
    return nonzero_e_values, nonzero_U_columns
    
#Make a rev1 of the compact_EVD function that actually uses a direct SVD on the Jacobian
#instead, but for compatibility returns the same output as the first revision compact_EVD function.
def compact_EVD_via_SVD(mat, threshold= 1e-10):
    """
    Generate the compact eigenvalue decomposition of the input matrix.
    Assumes of course that the user has specified a diagonalizable matrix,
    there are no safety checks for that made a priori.
    
    input:
    
    mat : ndarray
        input matrix we want the compact EVD for. Assumed to be diagonalizable.
        
    threshold : float, optional
        threshold value for deciding if an eigenvalue is zero.
        
    output:
    
    e : ndarray
        1-D numpy array of the non-zero eigenvalues of mat.
    U : ndarray
        Matrix such that U@diag(s)@U.conj().T=mat.
    """
    
    #take the SVD of mat.
    try:
        _, s, Vh = _np.linalg.svd(mat)
    except _np.linalg.LinAlgError:
        print('SVD Calculation Failed to Converge.')
        print('Falling back to Scipy SVD with lapack driver gesvd, which is slower but *should* be more stable.')
        _, s, Vh = _sla.svd(mat, lapack_driver='gesvd')

    #How many non-zero eigenvalues are there and what are their indices
    nonzero_eigenvalue_indices= _np.nonzero(_np.abs(s)>threshold)

    #extract the corresponding columns and values fom U and s:
    #For EVD/eigh We want the columns of U and the rows of Uh:
    nonzero_e_values = s[nonzero_eigenvalue_indices]**2
    nonzero_U_columns = Vh.T[:, nonzero_eigenvalue_indices[0]]
    
    return nonzero_e_values, nonzero_U_columns    


#Function for generating an "update cache" of pre-computed matrices which will be
#reused during a sequence of many additive updates to the same base matrix.

def construct_update_cache(mat):
    """
    Calculates the parts of the eigenvalue update loop algorithm that we can 
    pre-compute and reuse throughout all of the potential updates.
    
    Input:
    
    mat : ndarray
        The matrix to construct a set of reusable objects for performing the updates.
        mat is assumed to be a symmetric square matrix.
        
    Output:
    
    U, e : ndarrays
        The components of the compact eigenvalue decomposition of mat
        such that U@diag(s)@U.conj().T= mat
        e in this case is a 1-D array of the non-zero eigenvalues.
    projU : ndarray
        A projector onto the complement of the column space of U
        Corresponds to (I-U@U.T)
    """
    
    #Start by constructing a compact EVD of the input matrix. 
    e, U = compact_EVD(mat)
    
    #construct the projector
    #I think the conjugation is superfluous when we have real
    #eigenvectors which in principle we should if using eigh
    #for the compact EVD calculation. 
    projU= _np.eye(mat.shape[0]) - U@U.T
    
    #I think that's all we can pre-compute, so return those values:
    
    #I don't actually need the value of U
    #Nope, that's wrong. I do for the construction of K.
    return e, U, projU
    

#Function that wraps up all of the work for performing the updates.
    
def symmetric_low_rank_spectrum_update(update, orig_e, U, proj_U, force_rank_increase=False):
    """
    This function performs a low-rank update to the spectrum of
    a matrix. It takes as input a symmetric update of the form:
    A@A.T, in other words a symmetric rank-decomposition of the update
    matrix. Since the update is symmetric we only pass as input one
    half (i.e. we only need A, since A.T in numpy is treated simply
    as a different view of A). We also pass in the original spectrum
    as well as a projector onto the complement of the column space
    of the original matrix's eigenvector matrix.
    
    input:
    
    update : ndarray
        symmetric low-rank update to perform.
        This is the first half the symmetric rank decomposition s.t.
        update@update.T= the full update matrix.
    
    orig_e : ndarray
        Spectrum of the original matrix. This is a 1-D array.
        
    proj_U : ndarray
        Projector onto the complement of the column space of the
        original matrix's eigenvectors.
        
    force_rank_increase : bool
        A flag to indicate whether we are looking to force a rank increase.
        If so, then after the rrqr calculation we can check the rank of the projection
        of the update onto the complement of the column space of the base matrix and
        abort early if that is zero.
    """
    
    #First we need to for the matrix P, whose column space
    #forms an orthonormal basis for the component of update
    #that is in the complement of U.
    proj_update= proj_U@update
    
    #Next take the RRQR decomposition of this matrix:
    q_update, r_update, _ = _sla.qr(proj_update, mode='economic', pivoting=True)
    
    #Construct P by taking the columns of q_update corresponding to non-zero values of r_A on the diagonal.
    nonzero_indices_update= _np.nonzero(_np.abs(_np.diag(r_update))>1e-10) #HARDCODED
    
    #print the rank of the orthogonal complement if it is zero.
    if len(nonzero_indices_update[0])==0:
        #print('Zero Rank Orthogonal Complement Found')
        return None, False
    
    P= q_update[: , nonzero_indices_update[0]]
    
    #Now form the matrix R_update which is given by P.T @ proj_update.
    R_update= P.T@proj_update
    
    #R_update gets concatenated with U.T@update to form
    #a block column matrix
    block_column= _np.concatenate([U.T@update, R_update], axis=0)
    
    #We now need to construct the K matrix, which is given by
    #E+ block_column@block_column.T where E is a matrix with eigenvalues
    #on the diagonal with an appropriate number of zeros padded.
    
    #Instead of explicitly constructing the diagonal matrix of eigenvalues
    #I'll use einsum to construct a view of block_column@block_column.T's
    #diagonal and do an in-place sum directly to it.
    K= block_column@block_column.T
    
    #construct a view of the diagonal of K
    K_diag= _np.einsum('ii->i', K)
    
    #Get the dimension of K so we know how many zeros to pad the original eigenvalue
    #list with.
    K_diag+= _np.pad(orig_e, (0, (K.shape[0]-len(orig_e))) )
    
    #Since K_diag was a view of the original matrix K, this should have
    #modified the original K matrix in-place.
    
    #Now we need to get the spectrum of K, i.e. the spectrum of the 
    #updated matrices
    #I don't actually need the eigenvectors, so we don't need to output these
    new_evals= _np.linalg.eigvalsh(K)
    
    #return the new eigenvalues
    return new_evals, True
 
#Note: This function won't work for our purposes because of the assumptions
#about the rank of the update on the nullspace of the matrix we're updating,
#but keeping this here commented for future reference.
#Function for doing fast calculation of the updated inverse trace:
#def riedel_style_inverse_trace(update, orig_e, U, proj_U, force_rank_increase=True):
#    """
#    input:
#    
#    update : ndarray
#        symmetric low-rank update to perform.
#        This is the first half the symmetric rank decomposition s.t.
#        update@update.T= the full update matrix.
#    
#    orig_e : ndarray
#        Spectrum of the original matrix. This is a 1-D array.
#        
#    proj_U : ndarray
#        Projector onto the complement of the column space of the
#        original matrix's eigenvectors.
#        
#    output:
#    
#    trace : float
#        Value of the trace of the updated psuedoinverse matrix.
#    
#    updated_rank : int
#        total rank of the updated matrix.
#        
#    rank_increase_flag : bool
#        a flag that is returned to indicate is a candidate germ failed to amplify additional parameters. 
#        This indicates things short circuited and so the scoring function should skip this germ.
#    """
#    
#    #First we need to for the matrix P, whose column space
#    #forms an orthonormal basis for the component of update
#    #that is in the complement of U.
#    
#    print('proj_U Shape: ', proj_U.shape)
#    print('update shape: ', update.shape)
#    
#    proj_update= proj_U@update
#    
#    #Next take the RRQR decomposition of this matrix:
#    q_update, r_update, _ = _sla.qr(proj_update, mode='economic', pivoting=True)
#    
#    #Construct P by taking the columns of q_update corresponding to non-zero values of r_A on the diagonal.
#    nonzero_indices_update= _np.nonzero(_np.diag(r_update)>1e-10) #HARDCODED (threshold is hardcoded)
#    
#    #if the rank doesn't increase then we can't use the Riedel approach.
#    #Abort early and return a flag to indicate the rank did not increase.
#    if len(nonzero_indices_update[0])==0 and force_rank_increase:
#        return None, None, False
#    
#    print('proj_update shape: ', proj_update.shape)
#    
#    P= q_update[: , nonzero_indices_update[0]]
#    
#    print('P shape: ', P.shape)
#    
#    updated_rank= len(orig_e)+ len(nonzero_indices_update[0])
#    
#    
#    print('Change in rank= ',updated_rank-len(orig_e))
#    
#    #Now form the matrix R_update which is given by P.T @ proj_update.
#    R_update= P.T@proj_update
#    
#    print('R_update Shape: ', R_update.shape)
#    
#    #R_update gets concatenated with U.T@update to form
#    #a block column matrixblock_column= np.concatenate([U.T@update, R_update], axis=0)    
#    
#    Uta= U.T@update
#    
#    try:
#        RRRDinv= R_update@_np.linalg.inv(R_update.T@R_update) 
#    except _np.linalg.LinAlgError as err:
#        print('Numpy thinks this matrix is singular, condition number is: ', _np.linalg.cond(R_update.T@R_update))
#        print((R_update.T@R_update).shape)
#        raise err
#    pinv_orig_e_mat= _np.diag(1/orig_e)
#    
#    #print out a bunch of shapes for debugging:
#    
#    print('RRD Shape: ',  (R_update.T@R_update).shape)
#    
#    print('RRRDinv shape: ', RRRDinv.shape)
#    print('Uta^T shape: ', (Uta.T).shape)
#    print('pinv_orig_e_mat shape: ', pinv_orig_e_mat.shape)
#    print('Uta shape: ', Uta.shape)
#    print('RRRDinv^T shape: ', RRRDinv.T.shape)
#    
#    trace= _np.sum(1/orig_e) + _np.trace( RRRDinv@(_np.eye(Uta.shape[1]) + Uta.T@pinv_orig_e_mat@Uta)@RRRDinv.T )
#    
#    return trace, updated_rank, True
    
def minamide_style_inverse_trace(update, orig_e, U, proj_U, force_rank_increase=True):
    """
    This function performs a low-rank update to the components of
    the psuedo inverse of a matrix relevant to the calculation of that
    matrix's updated trace. It takes as input a symmetric update of the form:
    A@A.T, in other words a symmetric rank-decomposition of the update
    matrix. Since the update is symmetric we only pass as input one
    half (i.e. we only need A, since A.T in numpy is treated simply
    as a different view of A). We also pass in the original spectrum
    as well as a projector onto the complement of the column space
    of the original matrix's eigenvector matrix.
    
    Based on an update formula for psuedoinverses by minamide combined with
    a result on updating compact SVDs by M. Brand.
    
    input:
    
    update : ndarray
        symmetric low-rank update to perform.
        This is the first half the symmetric rank decomposition s.t.
        update@update.T= the full update matrix.
    
    orig_e : ndarray
        Spectrum of the original matrix. This is a 1-D array.
        
    proj_U : ndarray
        Projector onto the complement of the column space of the
        original matrix's eigenvectors.
        
    updated_trace : float
        Value of the trace of the updated psuedoinverse matrix.
    
    updated_rank : int
        total rank of the updated matrix.
        
    rank_increase_flag : bool
        a flag that is returned to indicate is a candidate germ failed to amplify additional parameters. 
        This indicates things short circuited and so the scoring function should skip this germ.
    """

    #First we need to for the matrix P, whose column space
    #forms an orthonormal basis for the component of update
    #that is in the complement of U.
    proj_update= proj_U@update
    
    #Next take the RRQR decomposition of this matrix:
    q_update, r_update, _ = _sla.qr(proj_update, mode='economic', pivoting=True)
    
    #Construct P by taking the columns of q_update corresponding to non-zero values of r_A on the diagonal.
    nonzero_indices_update= _np.nonzero(_np.abs(_np.diag(r_update))>1e-10)
    
    #if the rank doesn't increase then we can't use the Riedel approach.
    #Abort early and return a flag to indicate the rank did not increase.
    if len(nonzero_indices_update[0])==0 and force_rank_increase:
        return None, None, False
    
    updated_rank= len(orig_e)+ len(nonzero_indices_update[0])
    P= q_update[: , nonzero_indices_update[0]]
    
    #Now form the matrix R_update which is given by P.T @ proj_update.
    R_update= P.T@proj_update
    
    #Get the psuedoinverse of R_update:
    try:
        pinv_R_update= _np.linalg.pinv(R_update, rcond=1e-10) #hardcoded
    except _np.linalg.LinAlgError:
        #This means the SVD did not converge, try to fall back to a more stable
        #SVD implementation using the scipy lapack_driver options.
        print('pinv Calculation Failed to Converge.')
        print('Falling back to pinv implementation based on Scipy SVD with lapack driver gesvd, which is slower but *should* be more stable.')
        pinv_R_update = stable_pinv(R_update)
        
    #I have a bunch of intermediate matrices I need to construct. Some of which are used to build up
    #subsequent ones.
    beta= U.T@update
    
    gamma = pinv_R_update.T @ beta.T
    
    #column vector of the original eigenvalues.
    orig_e_inv= _np.reshape(1/orig_e, (len(orig_e),1))
    
    pinv_E_beta= orig_e_inv*beta
    
    B= _np.eye(pinv_R_update.shape[0]) - pinv_R_update @ R_update
    
    Dinv_chol= _np.linalg.cholesky(_np.linalg.inv(_np.eye(pinv_R_update.shape[0]) + B@(pinv_E_beta.T@pinv_E_beta)@B))
    
    pinv_E_beta_B_Dinv_chol= pinv_E_beta@B@Dinv_chol
    
    #Now construct the two matrices we need:  
    #numpy einsum based approach for the upper left block:
    upper_left_block_diag = _np.einsum('ij,ji->i', pinv_E_beta_B_Dinv_chol, pinv_E_beta_B_Dinv_chol.T) + _np.reshape(orig_e_inv, (len(orig_e), ))
    
    
    #The lower right seems fast enough as it is for now, but we can try an einsum style direct diagonal
    #calculation if need be.
    lower_right_block= (gamma@(orig_e_inv*gamma.T))+ pinv_R_update.T@pinv_R_update - gamma@pinv_E_beta_B_Dinv_chol@pinv_E_beta_B_Dinv_chol.T@gamma.T
    
    #the updated trace should just be the trace of these two matrices:
    updated_trace= _np.sum(upper_left_block_diag) + _np.trace(lower_right_block)
    
    return updated_trace, updated_rank, True

    
#-------Modified Germ Selection Algorithm-------------------%

#This version of the algorithm adds support for using low-rank
#updates to speed the calculation of eigenvalues for additive
#updates.
    
def find_germs_breadthfirst_rev1(model_list, germs_list, randomize=True,
                            randomization_strength=1e-3, num_copies=None, seed=0,
                            op_penalty=0, score_func='all', tol=1e-6, threshold=1e6,
                            check=False, force="singletons", pretest=True, mem_limit=None,
                            comm=None, profiler=None, verbosity=0, num_nongauge_params=None,
                            num_gauge_params= None, float_type= _np.cdouble, 
                            mode="all-Jac", force_rank_increase=False,
                            save_cevd_cache_filename=None, load_cevd_cache_filename=None,
                            file_compression=False):
    """
    Greedy algorithm starting with 0 germs.

    Tries to minimize the number of germs needed to achieve amplificational
    completeness (AC). Begins with 0 germs and adds the germ that increases the
    score used to check for AC by the largest amount (for the model that
    currently has the lowest score) at each step, stopping when the threshold
    for AC is achieved. This strategy is something of a "breadth-first"
    approach, in contrast to :func:`find_germs_depthfirst`, which only looks at the
    scores for one model at a time until that model achieves AC, then
    turning it's attention to the remaining models.

    Parameters
    ----------
    model_list : Model or list
        The model or list of `Model`s to select germs for.

    germs_list : list of Circuit
        The list of germs to contruct a germ set from.

    randomize : bool, optional
        Whether or not to randomize `model_list` (usually just a single
        `Model`) with small (see `randomizationStrengh`) unitary maps
        in order to avoid "accidental" symmetries which could allow for
        fewer germs but *only* for that particular model.  Setting this
        to `True` will increase the run time by a factor equal to the
        numer of randomized copies (`num_copies`).

    randomization_strength : float, optional
        The strength of the unitary noise used to randomize input Model(s);
        is passed to :func:`~pygsti.objects.Model.randomize_with_unitary`.

    num_copies : int, optional
        The number of randomized models to create when only a *single* gate
        set is passed via `model_list`.  Otherwise, `num_copies` must be set
        to `None`.

    seed : int, optional
        Seed for generating random unitary perturbations to models.

    op_penalty : float, optional
        Coefficient for a penalty linear in the sum of the germ lengths.

    score_func : {'all', 'worst'}, optional
        Sets the objective function for scoring the eigenvalues. If 'all',
        score is ``sum(1/eigenvalues)``. If 'worst', score is
        ``1/min(eiganvalues)``.

    tol : float, optional
        Tolerance (`eps` arg) for :func:`_compute_bulk_twirled_ddd`, which sets
        the differece between eigenvalues below which they're treated as
        degenerate.

    threshold : float, optional
        Value which the score (before penalties are applied) must be lower than
        for a germ set to be considered AC.

    check : bool, optional
        Whether to perform internal checks (will slow down run time
        substantially).

    force : list of Circuits
        A list of `Circuit` objects which *must* be included in the final
        germ set.  If the special string "singletons" is given, then all of
        the single gates (length-1 sequences) must be included.

    pretest : boolean, optional
        Whether germ list should be initially checked for completeness.

    mem_limit : int, optional
        A rough memory limit in bytes which restricts the amount of intermediate
        values that are computed and stored.

    comm : mpi4py.MPI.Comm, optional
        When not None, an MPI communicator for distributing the computation
        across multiple processors.

    profiler : Profiler, optional
        A profiler object used for to track timing and memory usage.

    verbosity : int, optional
        Level of detail printed to stdout.

    num_nongauge_params : int, optional
        Force the number of nongauge parameters rather than rely on automated gauge optimization.
        
    float_type : numpy dtype object, optional
        Use an alternative data type for the values of the numpy arrays generated.
        
    force_rank_increase : bool, optional
        Whether to force the greedy iteration to select a new germ that increases the rank
        of the jacobian at each iteration (this may result in choosing a germ that is sub-optimal
        with respect to the chosen score function). Also results in pruning in subsequent
        optimization iterations. Defaults to False.

    Returns
    -------
    list
        A list of the built-up germ set (a list of :class:`Circuit` objects).
    """
    if comm is not None and comm.Get_size() > 1:
        from mpi4py import MPI  # not at top so pygsti doesn't require mpi4py

    printer = _baseobjs.VerbosityPrinter.create_printer(verbosity, comm)

    model_list = _setup_model_list(model_list, randomize,
                                   randomization_strength, num_copies, seed)

    dim = model_list[0].dim
    #Np = model_list[0].num_params #wrong:? includes spam...
    Np = model_list[0].num_params
    #print("DB Np = %d, Ng = %d" % (Np,Ng))
    assert(all([(mdl.dim == dim) for mdl in model_list])), \
        "All models must have the same dimension!"
    #assert(all([(mdl.num_params == Np) for mdl in model_list])), \
    #    "All models must have the same number of parameters!"
    
    if (num_nongauge_params is None) or (num_gauge_params is None):
        (_, numGaugeParams,
         numNonGaugeParams, _) = _get_model_params(model_list)
        if num_nongauge_params is not None:
            numGaugeParams = numGaugeParams + numNonGaugeParams - num_nongauge_params
            numNonGaugeParams = num_nongauge_params
    elif (num_nongauge_params is not None) and  (num_gauge_params is not None):
        numGaugeParams = num_gauge_params
        numNonGaugeParams = num_nongauge_params
    
    printer.log('Number of gauge parameters: ' + str(numGaugeParams), 1) 
    printer.log('Number of non-gauge parameters: ' + str(numNonGaugeParams), 1)

    germLengths = _np.array([len(germ) for germ in germs_list], _np.int64)

    numGerms = len(germs_list)

    goodGerms = []
    weights = _np.zeros(numGerms, _np.int64)
    if force:
        if force == "singletons":
            weights[_np.where(germLengths == 1)] = 1
            goodGerms = [germ for i, germ in enumerate(germs_list) if germLengths[i] == 1]
        else:  # force should be a list of Circuits
            for opstr in force:
                weights[germs_list.index(opstr)] = 1
            goodGerms = force[:]
            
    #We should do the memory estimates before the pretest:
    FLOATSIZE= float_type(0).itemsize

    memEstimatealljac = FLOATSIZE * len(model_list) * len(germs_list) * Np**2
    # for _compute_bulk_twirled_ddd
    memEstimatealljac += FLOATSIZE * len(model_list) * len(germs_list) * dim**2 * Np
    # for _bulk_twirled_deriv sub-call
    printer.log("Memory estimate of %.1f GB for all-Jac mode." %
                (memEstimatealljac / 1024.0**3), 1)            

    memEstimatesinglejac = FLOATSIZE * 3 * len(model_list) * Np**2 + \
        FLOATSIZE * 3 * len(model_list) * dim**2 * Np
    #Factor of 3 accounts for currentDDDs, testDDDs, and bestDDDs
    printer.log("Memory estimate of %.1f GB for single-Jac mode." %
                (memEstimatesinglejac / 1024.0**3), 1)            

    if mem_limit is not None:
        
        printer.log("Memory limit of %.1f GB specified." %
            (mem_limit / 1024.0**3), 1)
    
        if memEstimatesinglejac > mem_limit:
                raise MemoryError("Too little memory, even for single-Jac mode!")
    
        if mode=="all-Jac" and (memEstimatealljac > mem_limit):
            #fall back to single-Jac mode
            
            printer.log("Not enough memory for all-Jac mode, falling back to single-Jac mode.", 1)
            
            mode = "single-Jac"  # compute a single germ's jacobian at a time    

    if pretest:
        undercompleteModelNum = test_germs_list_completeness(model_list,
                                                             germs_list,
                                                             score_func,
                                                             threshold,
                                                             float_type=float_type)
        if undercompleteModelNum > -1:
            printer.warning("Complete initial germ set FAILS on model "
                            + str(undercompleteModelNum) + ".")
            printer.warning("Aborting search.")
            return None

        printer.log("Complete initial germ set succeeds on all input models.", 1)
        printer.log("Now searching for best germ set.", 1)

    printer.log("Starting germ set optimization. Lower score is better.", 1) 

    twirledDerivDaggerDerivList = None

    if mode == "all-Jac":
        twirledDerivDaggerDerivList = \
            [_compute_bulk_twirled_ddd(model, germs_list, tol,
                                       check, germLengths, comm, float_type=float_type)
             for model in model_list]
        print('Numpy Array Data Type:', twirledDerivDaggerDerivList[0].dtype)
        printer.log("Numpy array data type for twirled derivatives is: "+ str(twirledDerivDaggerDerivList[0].dtype)+
                    " If this isn't what you specified then something went wrong.", 1) 
        
        #print out some information on the rank of the J^T J matrices for each germ.
        #matrix_ranks=_np.linalg.matrix_rank(twirledDerivDaggerDerivList[0])
        #print('J^T J dimensions: ', twirledDerivDaggerDerivList[0].shape)
        #print('J^T J Ranks: ', matrix_ranks)
        #print('Rank Ratio To Num Parameters: ', matrix_ranks/twirledDerivDaggerDerivList[0].shape[1])
                    
        currentDDDList = []
        for i, derivDaggerDeriv in enumerate(twirledDerivDaggerDerivList):
            currentDDDList.append(_np.sum(derivDaggerDeriv[_np.where(weights == 1)[0], :, :], axis=0))

    elif mode == "single-Jac":
        currentDDDList = [_np.zeros((Np, Np), dtype=float_type) for mdl in model_list]

        loc_Indices, _, _ = _mpit.distribute_indices(
            list(range(len(goodGerms))), comm, False)

        with printer.progress_logging(3):
            for i, goodGermIdx in enumerate(loc_Indices):
                printer.show_progress(i, len(loc_Indices),
                                      prefix="Initial germ set computation",
                                      suffix=germs_list[goodGermIdx].str)
                #print("DB: Rank%d computing initial index %d" % (comm.Get_rank(),goodGermIdx))

                for k, model in enumerate(model_list):
                    currentDDDList[k] += _compute_twirled_ddd(
                        model, germs_list[goodGermIdx], tol, float_type=float_type)

        #aggregate each currendDDDList across all procs
        if comm is not None and comm.Get_size() > 1:
            for k, model in enumerate(model_list):
                result = _np.empty((Np, Np), dtype=float_type)
                comm.Allreduce(currentDDDList[k], result, op=MPI.SUM)
                currentDDDList[k][:, :] = result[:, :]
                result = None  # free mem
                
    elif mode== "compactEVD":
        #implement a new caching scheme which takes advantage of the fact that the J^T J matrices are typically
        #rather sparse. Instead of caching the J^T J matrices for each germ we'll cache the compact EVD of these
        #and multiply the compact EVD components through each time we need one.
        
        if load_cevd_cache_filename is not None:
            printer.log('Loading Compact EVD Cache From Disk',1)
            with _np.load(load_cevd_cache_filename) as cevd_cache:
                twirledDerivDaggerDerivList=[list(cevd_cache.values())]
            
        else: 
            twirledDerivDaggerDerivList = \
                [_compute_bulk_twirled_ddd_compact(model, germs_list, tol,
                                                  evd_tol=1e-10, float_type=float_type, printer=printer)
             for model in model_list]
             
            if save_cevd_cache_filename is not None:
                if len(twirledDerivDaggerDerivList)>1:
                    raise ValueError('Currently not configured to save compactEVD caches to disk when there is more than one model in the model list. i.e. this is not currently compatible with model randomization to get the non-lite germs.')
                #otherwise conver the first entry of twirledDerivDaggerDerivList,
                #which itself a list of a half of the symmetric rank decompositions
                #and save it to disk using _np.savez or _np.savez_compressed
                printer.log('Saving Compact EVD Cache to Disk', 1)
                if file_compression:
                    _np.savez_compressed(save_cevd_cache_filename,*twirledDerivDaggerDerivList[0])
                else:
                    _np.savez(save_cevd_cache_filename,*twirledDerivDaggerDerivList[0])
                
             #_compute_bulk_twirled_ddd_compact returns a tuple with two lists
             #corresponding to the U@diag(sqrt(2)), e  matrices for each germ's J^T J matrix's_list
             #compact evd.
        currentDDDList = []
        nonzero_weight_indices= _np.nonzero(weights)
        nonzero_weight_indices= nonzero_weight_indices[0]
        for i, derivDaggerDeriv in enumerate(twirledDerivDaggerDerivList):
            #reconstruct the needed J^T J matrices
            for j, idx in enumerate(nonzero_weight_indices):
                if j==0:
                    temp_DDD = derivDaggerDeriv[idx] @ derivDaggerDeriv[idx].T
                else:
                    temp_DDD += derivDaggerDeriv[idx] @ derivDaggerDeriv[idx].T
                    
                #print('temp_DDD shape= ',temp_DDD.shape) 
            currentDDDList.append(temp_DDD)

    else:  # should be unreachable since we set 'mode' internally above
        raise ValueError("Invalid mode: %s" % mode)  # pragma: no cover

    # Dict of keyword arguments passed to compute_score_non_AC that don't
    # change from call to call
    nonAC_kwargs = {
        'score_fn': lambda x: _scoring.list_score(x, score_func=score_func),
        'threshold_ac': threshold,
        'num_nongauge_params': numNonGaugeParams,
        'op_penalty': op_penalty,
        'germ_lengths': germLengths,
        'float_type': float_type,
    }

    initN = 1
    while _np.any(weights == 0):
        printer.log("Outer iteration: %d of %d amplified, %d germs" %
                    (initN, numNonGaugeParams, len(goodGerms)), 2)
        # As long as there are some unused germs, see if you need to add
        # another one.
        if initN == numNonGaugeParams:
            break   # We are AC for all models, so we can stop adding germs.

        candidateGermIndices = _np.where(weights == 0)[0]
        loc_candidateIndices, owners, _ = _mpit.distribute_indices(
            candidateGermIndices, comm, False)

        # Since the germs aren't sufficient, add the best single candidate germ
        bestDDDs = None
        bestGermScore = _scoring.CompositeScore(1.0e100, 0, None)  # lower is better
        iBestCandidateGerm = None
        
        if mode=="compactEVD":
            #calculate the update cache for each element of currentDDDList 
            printer.log('Creating update cache.')
            currentDDDList_update_cache = [construct_update_cache(currentDDD) for currentDDD in currentDDDList]
            #the return value of the update cache is a tuple with the elements
            #(e, U, projU)    
        with printer.progress_logging(2):
            for i, candidateGermIdx in enumerate(loc_candidateIndices):
                printer.show_progress(i, len(loc_candidateIndices),
                                      prefix="Inner iter over candidate germs",
                                      suffix=germs_list[candidateGermIdx].str)

                #print("DB: Rank%d computing index %d" % (comm.Get_rank(),candidateGermIdx))
                worstScore = _scoring.CompositeScore(-1.0e100, 0, None)  # worst of all models

                # Loop over all models
                testDDDs = []
                
                if mode == "all-Jac":
                    # Loop over all models
                    for k, currentDDD in enumerate(currentDDDList):
                        testDDD = currentDDD.copy()
                    
                        #just get cached value of deriv-dagger-deriv
                        derivDaggerDeriv = twirledDerivDaggerDerivList[k][candidateGermIdx]
                        testDDD += derivDaggerDeriv
                        
                        nonAC_kwargs['germ_lengths'] = \
                        _np.array([len(germ) for germ in
                                   (goodGerms + [germs_list[candidateGermIdx]])])
                        worstScore = max(worstScore, compute_composite_germ_set_score(
                                    partial_deriv_dagger_deriv=testDDD[None, :, :], init_n=initN,
                                    **nonAC_kwargs))
                        testDDDs.append(testDDD)  # save in case this is a keeper
                
                elif mode == "single-Jac":
                    # Loop over all models
                    for k, currentDDD in enumerate(currentDDDList):
                        testDDD = currentDDD.copy()
                        
                        #compute value of deriv-dagger-deriv
                        model = model_list[k]
                        testDDD += _compute_twirled_ddd(
                            model, germs_list[candidateGermIdx], tol, float_type=float_type)
                            
                        nonAC_kwargs['germ_lengths'] = \
                        _np.array([len(germ) for germ in
                                   (goodGerms + [germs_list[candidateGermIdx]])])
                        worstScore = max(worstScore, compute_composite_germ_set_score(
                                    partial_deriv_dagger_deriv=testDDD[None, :, :], init_n=initN,
                                    **nonAC_kwargs))
                        testDDDs.append(testDDD)  # save in case this is a keeper
                    
                    
                elif mode == "compactEVD":
                    # Loop over all models
                    for k, update_cache in enumerate(currentDDDList_update_cache):
                        #reconstruct the J^T J matrix from it's compact SVD
                        #testDDD += twirledDerivDaggerDerivList[k][0][candidateGermIdx] @ \
                        #           _np.diag(twirledDerivDaggerDerivList[k][1][candidateGermIdx]) @\
                        #           twirledDerivDaggerDerivList[k][2][candidateGermIdx]
                    # (else already checked above)
                    
                        #pass in the 
                    
                        nonAC_kwargs['germ_lengths'] = \
                            _np.array([len(germ) for germ in
                                       (goodGerms + [germs_list[candidateGermIdx]])])
                        nonAC_kwargs['num_params']=Np
                        nonAC_kwargs['force_rank_increase']= force_rank_increase
                        
                        
                        if score_func=="worst":
                            worstScore = max(worstScore, compute_composite_germ_set_score_compactevd(
                                                current_update_cache= update_cache,
                                                germ_update=twirledDerivDaggerDerivList[k][candidateGermIdx], 
                                                init_n=initN, **nonAC_kwargs))
                        elif score_func=="all":
                            worstScore = max(worstScore, compute_composite_germ_set_score_low_rank_trace(
                                                current_update_cache= update_cache,
                                                germ_update=twirledDerivDaggerDerivList[k][candidateGermIdx], 
                                                init_n=initN, **nonAC_kwargs))
                            
                        

                # Take the score for the current germ to be its worst score
                # over all the models.
                germScore = worstScore
                printer.log(str(germScore), 4)
                if germScore < bestGermScore:
                    bestGermScore = germScore
                    iBestCandidateGerm = candidateGermIdx
                    
                    #If we are using the modes "all-Jac" or "single-Jac" then we will
                    #have been appending to testDDD throughout the process and can just set
                    #bestDDDs to testDDDs
                    if mode == "all-Jac" or mode == "single-Jac":
                        bestDDDs = testDDDs
                    
                    elif mode == "compactEVD":
                        #if compact EVD mode then we'll avoid reconstructing the J^T J matrix
                        #unless the germ is the current best.
                        bestDDDs= [currentDDD.copy() + \
                            twirledDerivDaggerDerivList[k][candidateGermIdx]@\
                            twirledDerivDaggerDerivList[k][candidateGermIdx].T\
                            for k, currentDDD in enumerate(currentDDDList)]
                testDDDs = None

        # Add the germ that gives the best germ score
        if comm is not None and comm.Get_size() > 1:
            #figure out which processor has best germ score and distribute
            # its information to the rest of the procs
            globalMinScore = comm.allreduce(bestGermScore, op=MPI.MIN)
            toSend = comm.Get_rank() if (globalMinScore == bestGermScore) \
                else comm.Get_size() + 1
            winningRank = comm.allreduce(toSend, op=MPI.MIN)
            bestGermScore = globalMinScore
            toCast = iBestCandidateGerm if (comm.Get_rank() == winningRank) else None
            iBestCandidateGerm = comm.bcast(toCast, root=winningRank)
            for k in range(len(model_list)):
                comm.Bcast(bestDDDs[k], root=winningRank)

        #Update variables for next outer iteration
        weights[iBestCandidateGerm] = 1
        initN = bestGermScore.N
        #print('Current Minor Score ', bestGermScore.minor)
        goodGerms.append(germs_list[iBestCandidateGerm])

        for k in range(len(model_list)):
            currentDDDList[k][:, :] = bestDDDs[k][:, :]
            bestDDDs[k] = None

            printer.log("Added %s to final germs (%s)" %
                        (germs_list[iBestCandidateGerm].str, str(bestGermScore)), 2)

    return goodGerms
    
def compute_composite_germ_set_score_compactevd(current_update_cache, germ_update, 
                                                score_fn="all", threshold_ac=1e6, init_n=1, model=None,
                                                 partial_germs_list=None, eps=None, num_germs=None,
                                                 op_penalty=0.0, l1_penalty=0.0, num_nongauge_params=None,
                                                 num_params=None, force_rank_increase=False,
                                                 germ_lengths=None, float_type=_np.cdouble):
    """
    Compute the score for a germ set when it is not AC against a model.

    Normally scores computed for germ sets against models for which they are
    not AC will simply be astronomically large. This is fine if AC is all you
    care about, but not so useful if you want to compare partial germ sets
    against one another to see which is closer to being AC. This function
    will see if the germ set is AC for the parameters corresponding to the
    largest `N` eigenvalues for increasing `N` until it finds a value of `N`
    for which the germ set is not AC or all the non gauge parameters are
    accounted for and report the value of `N` as well as the score.
    This allows partial germ set scores to be compared against one-another
    sensibly, where a larger value of `N` always beats a smaller value of `N`,
    and ties in the value of `N` are broken by the score for that value of `N`.

    Parameters
    ----------
    
    current_update_cache : tuple
        A tuple whose elements are the components of the current update cache
        for performing a low-rank update. Elements are (e, U , projU).
        
    germ_update : ndarray
        A numpy array corresponding to one half of the low-rank symmetric update to
        to perform.
    
    score_fn : callable
        A function that takes as input a list of sorted eigenvalues and returns
        a score for the partial germ set based on those eigenvalues, with lower
        scores indicating better germ sets. Usually some flavor of
        :func:`~pygsti.algorithms.scoring.list_score`.

    threshold_ac : float, optional
        Value which the score (before penalties are applied) must be lower than
        for the germ set to be considered AC.

    init_n : int
        The number of largest eigenvalues to begin with checking.

    model : Model, optional
        The model against which the germ set is to be scored. Not needed if
        `partial_deriv_dagger_deriv` is provided.

    partial_germs_list : list of Circuit, optional
        The list of germs in the partial germ set to be evaluated. Not needed
        if `partial_deriv_dagger_deriv` (and `germ_lengths` when
        ``op_penalty > 0``) are provided.

    eps : float, optional
        Used when calculating `partial_deriv_dagger_deriv` to determine if two
        eigenvalues are equal (see :func:`_bulk_twirled_deriv` for details). Not
        used if `partial_deriv_dagger_deriv` is provided.

    op_penalty : float, optional
        Coefficient for a penalty linear in the sum of the germ lengths.

    germ_lengths : numpy.array, optional
        The length of each germ. Not needed if `op_penalty` is ``0.0`` or
        `partial_germs_list` is provided.

    l1_penalty : float, optional
        Coefficient for a penalty linear in the number of germs.

    num_nongauge_params : int, optional
        Force the number of nongauge parameters rather than rely on automated gauge optimization.
    
    num_params : int
        Total number of model parameters.
    
    force_rank_increase : bool, optional
        Whether to force the greedy iteration to select a new germ that increases the rank
        of the jacobian at each iteration (this may result in choosing a germ that is sub-optimal
        with respect to the chosen score function). Also results in pruning in subsequent
        optimization iterations. Defaults to False.
    
    
    Returns
    -------
    CompositeScore
        The score for the germ set indicating how many parameters it amplifies
        and its numerical score restricted to those parameters.
    """
    
    if germ_lengths is None:
        raise ValueError("Must provide either germ_lengths or "
                                 "partial_germs_list when op_penalty != 0.0!")
   
    if num_nongauge_params is None:
        if model is None:
            raise ValueError("Must provide either num_gauge_params or model!")
        else:
            reduced_model = _remove_spam_vectors(model)
            num_nongauge_params = reduced_model.num_params - reduced_model.num_gauge_params
            #print('Number of Nongauge Parameters For ')

    # Calculate penalty scores
    if num_germs is not None:
        numGerms = num_germs
    else:
        numGerms= len(germ_lengths)
    l1Score = l1_penalty * numGerms
    opScore = 0.0
    if op_penalty != 0.0:
        opScore = op_penalty * _np.sum(germ_lengths)
    
    #calculate the updated eigenvalues
    updated_eigenvalues, rank_increase_flag = symmetric_low_rank_spectrum_update(germ_update, current_update_cache[0], current_update_cache[1], current_update_cache[2], force_rank_increase)
    
    N_AC = 0
    AC_score = _np.inf
    
    #check if the rank_increase_flag is set to False, if so then we failed
    #to increase the rank and so couldn't use the inverse trace update.
    if not rank_increase_flag:
        AC_score = -_np.inf
        N_AC = -_np.inf
    else:
        #I want compatibility eith the lines below that pick off just the non_gauge eigenvalues. Rather than
        #do some index gymnastics I'll just pad this eigenvalue list (which is compact) and make it the expected
        #length (num params). Pad on the left because the code below assumes eigenvalues in ascending order.
        padded_updated_eigenvalues= _np.pad(updated_eigenvalues, (num_params-len(updated_eigenvalues),0))

        #now pull out just the top num_nongauge_params eigenvalues
        observableEigenvals = padded_updated_eigenvalues[-num_nongauge_params:]

        #combinedDDD = _np.sum(partial_deriv_dagger_deriv, axis=0)
        #sortedEigenvals = _np.sort(_np.real(_nla.eigvalsh(combinedDDD)))
        #observableEigenvals = sortedEigenvals[-num_nongauge_params:]
    
        for N in range(init_n, len(observableEigenvals) + 1):
            scoredEigenvals = observableEigenvals[-N:]
            candidate_AC_score = score_fn(scoredEigenvals)
            if candidate_AC_score > threshold_ac:
                break   # We've found a set of parameters for which the germ set
                # is not AC.
            else:
                AC_score = candidate_AC_score
                N_AC = N

    # OLD Apply penalties to the minor score; major part is just #amplified
    #major_score = N_AC
    #minor_score = AC_score + l1Score + opScore

    # Apply penalties to the major score
    major_score = -N_AC + opScore + l1Score
    minor_score = AC_score
    ret = _scoring.CompositeScore(major_score, minor_score, N_AC)
    #DEBUG: ret.extra = {'opScore': opScore,
    #    'sum(germ_lengths)': _np.sum(germ_lengths), 'l1': l1Score}
    return ret

def compute_composite_germ_set_score_low_rank_trace(current_update_cache, germ_update, 
                                                score_fn="all", threshold_ac=1e6, init_n=1, model=None,
                                                 partial_germs_list=None, eps=None, num_germs=None,
                                                 op_penalty=0.0, l1_penalty=0.0, num_nongauge_params=None,
                                                 num_params=None, force_rank_increase=False,
                                                 germ_lengths=None, float_type=_np.cdouble):
    """
    Compute the score for a germ set when it is not AC against a model.

    Normally scores computed for germ sets against models for which they are
    not AC will simply be astronomically large. This is fine if AC is all you
    care about, but not so useful if you want to compare partial germ sets
    against one another to see which is closer to being AC. This function
    will see if the germ set is AC for the parameters corresponding to the
    largest `N` eigenvalues for increasing `N` until it finds a value of `N`
    for which the germ set is not AC or all the non gauge parameters are
    accounted for and report the value of `N` as well as the score.
    This allows partial germ set scores to be compared against one-another
    sensibly, where a larger value of `N` always beats a smaller value of `N`,
    and ties in the value of `N` are broken by the score for that value of `N`.

    Parameters
    ----------
    
    current_update_cache : tuple
        A tuple whose elements are the components of the current update cache
        for performing a low-rank update. Elements are (e, U , projU).
        
    germ_update : ndarray
        A numpy array corresponding to one half of the low-rank symmetric update to
        to perform.
    
    score_fn : callable
        A function that takes as input a list of sorted eigenvalues and returns
        a score for the partial germ set based on those eigenvalues, with lower
        scores indicating better germ sets. Usually some flavor of
        :func:`~pygsti.algorithms.scoring.list_score`.

    threshold_ac : float, optional
        Value which the score (before penalties are applied) must be lower than
        for the germ set to be considered AC.

    init_n : int
        The number of largest eigenvalues to begin with checking.

    model : Model, optional
        The model against which the germ set is to be scored. Not needed if
        `partial_deriv_dagger_deriv` is provided.

    partial_germs_list : list of Circuit, optional
        The list of germs in the partial germ set to be evaluated. Not needed
        if `partial_deriv_dagger_deriv` (and `germ_lengths` when
        ``op_penalty > 0``) are provided.

    eps : float, optional
        Used when calculating `partial_deriv_dagger_deriv` to determine if two
        eigenvalues are equal (see :func:`_bulk_twirled_deriv` for details). Not
        used if `partial_deriv_dagger_deriv` is provided.

    op_penalty : float, optional
        Coefficient for a penalty linear in the sum of the germ lengths.

    germ_lengths : numpy.array, optional
        The length of each germ. Not needed if `op_penalty` is ``0.0`` or
        `partial_germs_list` is provided.

    l1_penalty : float, optional
        Coefficient for a penalty linear in the number of germs.

    num_nongauge_params : int, optional
        Force the number of nongauge parameters rather than rely on automated gauge optimization.
    
    num_params : int
        Total number of model parameters.
    
    force_rank_increase : bool, optional
        Whether to force the greedy iteration to select a new germ that increases the rank
        of the jacobian at each iteration (this may result in choosing a germ that is sub-optimal
        with respect to the chosen score function). Also results in pruning in subsequent
        optimization iterations. Defaults to False.
    
    
    Returns
    -------
    CompositeScore
        The score for the germ set indicating how many parameters it amplifies
        and its numerical score restricted to those parameters.
    
    rank_increase_flag : bool
        A flag that indicates whether the candidate update germ increases the rank
        of the overall Jacobian.
    """
    
    if germ_lengths is None:
        raise ValueError("Must provide either germ_lengths or "
                                 "partial_germs_list when op_penalty != 0.0!")
   
    if num_nongauge_params is None:
        if model is None:
            raise ValueError("Must provide either num_gauge_params or model!")
        else:
            reduced_model = _remove_spam_vectors(model)
            num_nongauge_params = reduced_model.num_params - reduced_model.num_gauge_params

    # Calculate penalty scores
    if num_germs is not None:
        numGerms = num_germs
    else:
        numGerms= len(germ_lengths)
    l1Score = l1_penalty * numGerms
    opScore = 0.0
    if op_penalty != 0.0:
        opScore = op_penalty * _np.sum(germ_lengths)
    
    #calculate the updated eigenvalues
    inverse_trace, updated_rank, rank_increase_flag = minamide_style_inverse_trace(germ_update, current_update_cache[0], current_update_cache[1], current_update_cache[2], force_rank_increase)
    
    #check if the rank_increase_flag is set to False, if so then we failed
    #to increase the rank and so couldn't use the inverse trace update.
    if not rank_increase_flag:
        AC_score = -_np.inf
        N_AC = -_np.inf
    else:
        AC_score = inverse_trace
        N_AC = updated_rank
        
    # Apply penalties to the major score
    major_score = -N_AC + opScore + l1Score
    minor_score = AC_score
    ret = _scoring.CompositeScore(major_score, minor_score, N_AC)
    
    #print(ret)
    
    #TODO revisit what to do with the rank increase flag so that we can use
    #it to remove unneeded germs from the list of candidates.
    
    return ret#, rank_increase_flag

#Function for even faster kronecker products courtesy of stackexchange:
def fast_kron(a,b):
    #Don't really understand the numpy tricks going on here,
    #But this does appear to work correctly in testing and
    #it is indeed a decent amount faster, fwiw.
    return (a[:, None, :, None]*b[None, :, None, :]).reshape(a.shape[0]*b.shape[0],a.shape[1]*b.shape[1])
   
   
#Stabler implementation of the psuedoinverse using the alternative lapack driver for SVD:
def stable_pinv(mat):
    U, s, Vh = _sla.svd(mat, lapack_driver='gesvd', full_matrices=False)
    pinv_s= np.zeros((len(s),1))
    for i, sval in enumerate(s):
        if sval>1e-10: #HARDCODED
            pinv_s[i]= 1/sval
    
    #new form the psuedoinverse:
    pinv= Vh.T@(pinv_s*U.T)
    return pinv