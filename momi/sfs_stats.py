import collections as co
import functools as ft
from cached_property import cached_property
import autograd.numpy as np
from .compute_sfs import _expected_sfs_tensor_prod

import pandas as pd
import seaborn
from matplotlib import pyplot as plt


class SfsStats(object):
    def __init__(self, sampled_n_dict):
        self.sampled_n_dict = {p: n for p, n in sampled_n_dict.items()
                               if n > 0}

    def tensor_prod(self, derived_weights_dict):
        raise NotImplementedError

    @cached_property
    def denom(self):
        return self.tensor_prod({})

    def ordered_prob(self, subsample_dict,
                     fold=False):
        # The ordered probability for the subsample given by
        # subsample_dict.
        #
        # Parameters:
        # subsample_dict: dict of list
        #    dict mapping population to a list of 0s and 1s giving the
        #       ordered subsample within that population.

        if fold:
            rev_subsample = {p: 1 - np.array(s)
                             for p, s in subsample_dict.items()}

            return (self.ordered_prob(subsample_dict)
                    + self.ordered_prob(rev_subsample))

        derived_weights_dict = {}
        for pop, pop_subsample in subsample_dict.items():
            n = self.sampled_n_dict[pop]
            arange = np.arange(n+1)

            cnts = co.Counter(pop_subsample)

            prob = np.ones(n+1)
            for i in range(cnts[0]):
                prob *= (n - arange - i)
            for i in range(cnts[1]):
                prob *= (arange - i)
            for i in range(cnts[0] + cnts[1]):
                prob /= float(n - i)

            derived_weights_dict[pop] = prob

        return self.tensor_prod(derived_weights_dict) / self.denom

    def count_1100(self, A, B, C, O=None):
        # O=None -> O is the root population
        subsample_dict = co.defaultdict(list)
        subsample_dict[A].append(1)
        subsample_dict[B].append(1)
        subsample_dict[C].append(0)
        if O is not None:
            subsample_dict[O].append(0)

        return self.ordered_prob(
            subsample_dict,
            fold=(O is not None))

    def baba(self, A, B, C, *O):
        return self.count_1100(A, C, B, *O)

    def abba(self, A, B, C, *O):
        return self.count_1100(B, C, A, *O)

    def abba_baba(self, A, B, C, D=None):
        """
        Returns the ABBA-BABA (f4) statistic for testing admixture.

        :param str A: First population
        :param str B: Second population
        :param str C: Third population
        :param str D: Fourth population. If None, use ancestral allele.
        """
        return self.baba(A, B, C, D) - self.abba(A, B, C, D)

    def f4(self, A, B, C, D=None):
        """
        Same as :meth:`abba_baba`
        """
        return self.abba_baba(A, B, C, D)

    def f3(self, A, B, O):
        """
        Computes f3 statistic (O-A)*(O-B)

        :param str A: First population
        :param str B: Second population
        :param str O: Third population.
        """
        return self.f4(O, A, O, B)

    def f2(self, A, B):
        """
        Computes f2 statistic (A-B)*(A-B)

        :param str A: First population
        :param str B: Second population
        """
        return self.f4(A, B, A, B)

    def pattersons_d(self, A, B, C, D=None):
        """
        Returns Patterson's D, defined as (BABA-ABBA)/(BABA+ABBA).

        :param str A: First population
        :param str B: Second population
        :param str C: Third population
        :param str D: Fourth population. If None, use ancestral allele.
        """
        abba = self.abba(A, B, C, D)
        baba = self.baba(A, B, C, D)
        return (baba - abba) / (baba + abba)

    def greens_f(self, A, B, C, *O):
        # Estimate for the admixture of C into B in tree (((A,B),C),O)
        return self.abba_baba(A, B, C, *O) / self.abba_baba(A, C, C, *O)

    def f4_ratio(self, A, B, C, X, *O):
        # For tree (((A,B),C),O), and X admixed between B,C
        # an estimate for the admixture proportion from B
        # ref: Patterson et al 2012, Ancient Admixture in Human History, eq (4)
        return self.f4(X, C, A, *O) / self.f4(B, C, A, *O)

    def singleton_probs(self, pops):
        denom = None
        probs = {}
        for pop in pops:
            prob = self.ordered_prob(dict([
                (p, [1]) if p == pop else (p, [0])
                for p in pops]), fold=True)
            probs[pop] = prob
            if denom is None:
                denom = prob
            else:
                denom = denom + prob
        return {"probs": probs, "denom": 1-denom}


class ModelFitStats(SfsStats):
    """Class to compare expected vs. observed statistics of the SFS.

    All methods return :class:`JackknifeGoodnessFitStat` unless
    otherwise stated.

    Currently, all goodness-of-fit statistics are based on the multinomial
    SFS (i.e., the SFS normalized to be a probability distribution
    summing to 1). Thus the mutation rate has no effect on these statistics.

    See Patterson et al 2012, "Ancient Admixture in Human History",
    for definitions of f2, f3, f4 (abba-baba), and D statistics.

    Note this class does NOT get updated when the underlying
    ``demo_model`` changes; a new :class:`ModelFitStats` needs
    to be created to reflect any changes in the demography.

    :param momi.DemographicModel demo_model: Demography to compute expected \
    statistics under.

    :param int n_jackknife: Split the dataset into ``n_jackknife`` contiguous \
    blocks of SNPs, to compute standard deviations via block jackknife.

    :param momi.SnpAlleleCounts data: Dataset to compute observed SFS \
    statistics. By default, use the dataset associated with ``demo_model`` \
    if that has been set by :meth:`DemographicModel.set_data`

    :param dict sampled_n_dict: The number of samples to use \
    per population. SNPs with fewer than this number of samples \
    are ignored. The default is to use the \
    full sample size of the data, i.e. to remove all SNPs with any missing \
    data. For datasets with large amounts of missing data, \
    this could potentially lead to most or all SNPs being removed, so it is \
    important to specify a smaller sample size in such cases.
    """
    def __init__(self, demo_model, n_jackknife, data=None,
                 sampled_n_dict=None):
        if not (data or demo_model._data):
            raise ValueError("Need to provide data or call "
                             "DemographicModel.set_data() first")
        elif not data:
            data = demo_model._data

        if n_jackknife:
            data = data.chunk_data(n_jackknife)

        data = data.subset_populations(
            demo_model.leafs, demo_model._non_ascertained_pops)
        sfs = data.sfs

        if not sampled_n_dict:
            sampled_n_dict = dict(zip(sfs.sampled_pops, sfs.sampled_n))
        self.sampled_n_dict = sampled_n_dict

        self.empirical = ObservedSfsStats(sfs, self.sampled_n_dict)
        self.expected = ExpectedSfsStats(
            demo_model._get_demo(self.sampled_n_dict), [
                pop for pop, is_asc in zip(sfs.sampled_pops,
                                           sfs.ascertainment_pop)
                if is_asc])

    def tensor_prod(self, derived_weights_dict):
        """Compute rank-1 tensor products of the SFS, which can be used \
        to express a wide range of SFS-based statistics.

        More specifically, this computes the sum

        .. math:: \sum_{i,j,\ldots} SFS_{i,j,\ldots} w^{(1)}_i w^{(2)}_j \cdots

        where :math:`w^{(1)}_i` is the weight corresponding to SFS entries \
        with ``i`` derived alleles in population 1, etc. Note the SFS is \
        normalized to sum to 1 here (it is a probability).

        :param dict derived_weights_dict: Maps leaf populations to \
        vectors (:class:`numpy.ndarray`). If a population has ``n`` samples \
        then the corresponding vector ``w`` should have length ``n+1``, \
        with ``w[i]`` being the weight for SFS entries with ``i`` copies of \
        the derived allele in the population.

        :rtype: :class:`JackknifeGoodnessFitStat`
        """
        exp = self.expected.tensor_prod(derived_weights_dict)
        emp = self.empirical.tensor_prod(derived_weights_dict)

        exp = exp / self.expected.denom
        emp = emp / self.empirical.denom

        return JackknifeGoodnessFitStat(exp, emp.est, emp.jackknife)

    @property
    def denom(self):
        return 1.0

    def pairwise_diffs(self, exclude_pops=[],
                       exclude_singletons=False, plot=True):
        pops = [p for p in self.leafs if p not in exclude_pops]
        fstats = self.fstats(sampled_n_dict={
            p: 1 for p in pops})

        if exclude_singletons:
            s_probs = fstats.singleton_probs(pops)

        df = []
        for pop1 in pops:
            for pop2 in pops:
                if pop1 < pop2:
                    prob = fstats.ordered_prob({
                        pop1: [1], pop2: [0]}, fold=True)
                    if exclude_singletons:
                        prob = (
                            prob - s_probs["probs"][pop1] -
                            s_probs["probs"][pop2]) / s_probs[
                                "denom"]

                    penalty = np.log(prob.observed / prob.expected)
                    line = [pop1, pop2, penalty, prob.z_score]
                    print(*line)
                    df.append(line)
        ret = pd.DataFrame(sorted(df, key=lambda x: abs(x[-1]),
                                  reverse=True),
                           columns=["Pop1", "Pop2", "Penalty", "Z"])
        if plot:
            pivoted = ret.pivot(index="Pop1", columns="Pop2",
                                values="Z")
            plt.gcf().clear()
            seaborn.heatmap(pivoted, annot=True, center=0)
            plt.title("Residual (Observed-Expected) Z-scores")
            pass
        return ret

    @property
    def n_subsets(self):
        return self.empirical.n_subsets

    @property
    def n_jackknife_blocks(self):
        return self.empirical.n_jackknife_blocks


class ObservedSfsStats(SfsStats):
    def __init__(self, sfs, sampled_n_dict):
        is_ascertained = dict(zip(sfs.sampled_pops, sfs.ascertainment_pop))
        if sum(n for p, n in sampled_n_dict.items()
               if is_ascertained[p]) < 2:
            raise ValueError("sampled_n_dict must contain at least 2 ascertained alleles")
        self.sfs = sfs
        super(ObservedSfsStats, self).__init__(sampled_n_dict)

    def tensor_prod(self, derived_weights_dict):
        weighted_counts = self.sfs.configs.count_subsets(derived_weights_dict,
                                                         self.sampled_n_dict)

        # subtract out weights of monomorphic
        mono_anc = {}
        mono_der = {}
        for pop, asc in zip(self.sfs.sampled_pops, self.sfs.ascertainment_pop):
            try:
                v = derived_weights_dict[pop]
            except KeyError:
                try:
                    v = [1] * (self.sampled_n_dict[pop]+1)
                except KeyError:
                    continue
            if asc:
                mono_anc[pop] = [v[0]] + [0]*(len(v)-1)
                mono_der[pop] = [0]*(len(v)-1) + [v[-1]]
            else:
                mono_anc[pop] = v
                mono_der[pop] = v
        mono_anc = self.sfs.configs.count_subsets(
            mono_anc, self.sampled_n_dict)
        mono_der = self.sfs.configs.count_subsets(
            mono_der, self.sampled_n_dict)

        return JackknifeStat.from_chunks(
            self.sfs.freqs_matrix.T.dot(
                weighted_counts - mono_anc - mono_der))

    @property
    def n_subsets(self):
        return self.denom.est

    @property
    def n_jackknife_blocks(self):
        return self.sfs.n_loci


class ExpectedSfsStats(SfsStats):
    def __init__(self, demo, ascertainment_pops):
        self.demo = demo
        self.ascertainment_pops = ascertainment_pops
        super(ExpectedSfsStats, self).__init__(dict(zip(demo.sampled_pops,
                                                        demo.sampled_n)))

    def tensor_prod(self, derived_weights_dict):
        #sampled_pops, sampled_n = zip(*sorted(self.sampled_n_dict.items()))
        #demo = self.demo._get_multipop_moran(sampled_pops, sampled_n)
        demo = self.demo

        vecs = []
        for p, n in zip(demo.sampled_pops, demo.sampled_n):
            v = []
            try:
                row = derived_weights_dict[p]
            except KeyError:
                row = np.ones(n+1)
            assert len(row) == n+1

            if p in self.ascertainment_pops:
                v.append([row[0]] + [0.0] * n)  # all ancestral state
                v.append([0.0] * n + [row[-1]])  # all derived state
            else:
                for _ in range(2):
                    v.append(row)
            v.append(row)

            vecs.append(np.array(v))

        res = _expected_sfs_tensor_prod(vecs, demo)
        return res[2] - res[0] - res[1]


class JackknifeGoodnessFitStat(object):
    """
    Object returned by methods of :class:`ModelFitStats`.

    Basic arithmetic operations are supported, allowing to build
    up complex statistics out of simpler ones.

    The raw expected, observed, and jackknifed_array values
    can be accessed as attributes of this class.

    :param float expected: the expected value of the statistic
    :param float observed: the observed value of the statistic
    :param numpy.ndarray jackknifed_array: array of the jackknifed \
    values of the statistic.
    """
    def __init__(self, expected, observed, jackknifed_array):
        self.expected = expected
        self.observed = observed
        self.jackknifed_array = jackknifed_array

    @property
    def sd(self):
        """
        Standard deviation of the statistic, estimated via jackknife
        """
        resids = self.jackknifed_array - self.observed
        return np.sqrt(np.mean(resids**2) * (
            len(self.jackknifed_array) - 1))

    @property
    def z_score(self):
        """
        Z-score of the statistic, defined as (observed-expected)/sd
        """
        return (self.observed - self.expected) / self.sd

    def __repr__(self):
        return ("JackknifeGoodnessFitStat(expected={}, observed={},"
                " sd={}, z_score={})").format(self.expected, self.observed,
                                              self.sd, self.z_score)

    def __add__(self, other):
        other = self._get_other(other)
        return JackknifeGoodnessFitStat(
            self.expected + other.expected,
            self.observed + other.observed,
            self.jackknifed_array + other.jackknifed_array)

    def __radd__(self, other):
        return self + other

    def __sub__(self, other):
        return self + (-1)*other

    def __rsub__(self, other):
        return (self * (-1)) + other

    def __mul__(self, other):
        other = self._get_other(other)
        return JackknifeGoodnessFitStat(
            self.expected * other.expected,
            self.observed * other.observed,
            self.jackknifed_array * other.jackknifed_array)

    def __rmul__(self, other):
        return self * other

    def __pow__(self, other):
        other = self._get_other(other)
        return JackknifeGoodnessFitStat(
            self.expected ** other.expected,
            self.observed ** other.observed,
            self.jackknifed_array ** other.jackknifed_array)

    def __rpow__(self, other):
        other = self._get_other(other)
        return other ** self

    def __truediv__(self, other):
        return self * (other**-1)

    def __rtruediv__(self, other):
        return (self**-1) * other

    def _get_other(self, other):
        try:
            other.expected, other.observed, other.jackknifed_array
        except AttributeError:
            return type(self)(other, other, other)
        else:
            return other


def jackknife_arr_op(wrapped_op):
    @ft.wraps(wrapped_op)
    def wraps_op(self, other):
        try:
            other.est, other.jackknife
        except AttributeError:
            return wrapped_op(self, JackknifeStat(other, other))
        else:
            return wrapped_op(self, other)
    return wraps_op


class JackknifeStat(object):
    @classmethod
    def from_chunks(cls, x):
        tot = np.sum(x)
        return cls(tot, tot - x)

    def __init__(self, est, jackknife):
        self.est = est
        self.jackknife = jackknife

    def apply(self, fun):
        return JackknifeStat(fun(self.est),
                              fun(self.jackknife))

    @jackknife_arr_op
    def __add__(self, other):
        return JackknifeStat(self.est + other.est,
                              self.jackknife + other.jackknife)

    def __radd__(self, other):
        return self + other

    def __neg__(self):
        return JackknifeStat(-self.est, -self.jackknife)

    @jackknife_arr_op
    def __sub__(self, other):
        return self + (-other)

    def __rsub__(self, other):
        return -self + other

    @jackknife_arr_op
    def __mul__(self, other):
        return JackknifeStat(self.est * other.est,
                              self.jackknife * other.jackknife)

    def __rmul__(self, other):
        return self * other

    def __truediv__(self, other):
        return self * (other**-1)

    def __rtruediv__(self, other):
        return (self**-1) * other

    @jackknife_arr_op
    def __pow__(self, other):
        return JackknifeStat(self.est ** other.est,
                              self.jackknife ** other.jackknife)

    @jackknife_arr_op
    def __rpow__(self, other):
        return JackknifeStat(other.est ** self.est,
                              other.jackknife ** self.jackknife)

    @property
    def resids(self):
        return self.jackknife - self.est

    @property
    def var(self):
        return np.mean(self.resids**2) * (len(self.jackknife) - 1)

    @property
    def sd(self):
        return np.sqrt(self.var)

    @property
    def z_score(self):
        return self.est / self.sd

    def __repr__(self):
        return "JackknifeStat(est={}, sd={}) at {}".format(
            self.est, self.sd, hex(id(self)))

