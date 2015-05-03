from __future__ import division
import bisect
import networkx as nx
from cStringIO import StringIO
from Bio import Phylo
from size_history import ConstantHistory, ExponentialHistory, PiecewiseHistory

from autograd.numpy import isnan, exp,min

import newick
import sh, os, random
import itertools
from collections import Counter

## TODO: do some reorganization and cleanup of this file


def _to_nx(ms_cmd, *args, **kwargs):
    parser = MsCmdParser(*args, **kwargs)
    cmd_list = parser.sorted_cmd_list(ms_cmd)
    for event in cmd_list:
        parser.add_event(*event)
    return parser.to_nx()

class MsCmdParser(object):
    def __init__(self, *args, **kwargs):
        #self.params = params
        self.params_dict = dict(kwargs)
        for i,x in enumerate(args):
            self.params_dict[str(i)] = x

        self.events,self.edges,self.nodes = [],[],{}
        # roots is the set of nodes currently at the root of the graph
        self.roots = {}
        self.prev_time = 0.0
        self.cmd_list = []

    def get_time(self, event_type, *args):
        if event_type[0] == 'e':
            return self.getfloat(args[0])
        return 0.0

    def sorted_cmd_list(self, ms_cmd):
        unsorted_list = []
        for arg in ms_cmd.split():
            if arg[0] == '-' and arg[1].isalpha():
                curr_args = [arg[1:]]
                unsorted_list.append(curr_args)
            else:
                curr_args.append(arg)       
        assert unsorted_list[0][0] == 'I'

        n_leaf_pop = self.getint(unsorted_list[0][1])

        sorted_cmd,times = [],[]
        sorted_pops = ['#'+str(i) for i in range(1,n_leaf_pop+1)]
        pop_times = [0.0]*n_leaf_pop
        for cmd in unsorted_list:
            t = self.get_time(cmd[0], *cmd[1:])
            idx = bisect.bisect_right(times, t)
            times.insert(idx, t)
            sorted_cmd.insert(idx, cmd)

            if cmd[0] == 'es':
                idx = bisect.bisect_right(pop_times, t)
                pop_times.insert(idx, t)
                sorted_pops.insert(idx, '#'+str(len(sorted_pops)+1))
        assert sorted_cmd[0][0] == 'I'

        self.pop_by_order = {x : i for i,x in enumerate(sorted_pops,1)}
        return sorted_cmd

    def add_event(self, event_flag, *args):
        # add nodes,edges,events associated with the event
        # and get back the arguments with variables substituted in
        args = getattr(self, '_' + event_flag)(*args)
        t = self.get_time(event_flag, *args)
        assert t >= self.prev_time
        self.prev_time = t
        self.cmd_list.append("-%s %s" % (event_flag, " ".join(map(str,args))))

    def getpop(self, pop):
        if not isinstance(pop, str):
            return pop
        if pop[0] == '#':
            return self.pop_by_order[pop]
        return self.getint(pop)

    def _es(self, t,i,p):
        t,p = map(self.getfloat, (t,p))
        i = self.getpop(i)

        child = self.roots[i]
        self.set_sizes(self.nodes[child], t)

        parents = ((child,), len(self.roots)+1)
        assert all([par not in self.nodes for par in parents])

        self.nodes[child]['splitprobs'] = {par : prob for par,prob in zip(parents, [p,1-p])}

        prev = self.nodes[child]['sizes'][-1]
        self.nodes[parents[0]] = {'sizes':[{'t':t,'N':prev['N_top'], 'alpha':prev['alpha']}]}
        self.nodes[parents[1]] = {'sizes':[{'t':t,'N':1.0, 'alpha':None}]}

        new_edges = tuple([(par, child) for par in parents])
        self.events.append( new_edges )
        self.edges += list(new_edges)

        self.roots[i] = parents[0]
        self.roots[len(self.roots)+1] = parents[1]
        
        return t,i,p

    def _ej(self, t,i,j):
        t = self.getfloat(t)
        i,j = map(self.getpop, [i,j])

        for k in i,j:
            # sets the TruncatedSizeHistory, and N_top and alpha for all epochs
            self.set_sizes(self.nodes[self.roots[k]], t)

        new_pop = (self.roots[i], self.roots[j])
        self.events.append( ((new_pop,self.roots[i]),
                        (new_pop,self.roots[j]))  )

        assert new_pop not in self.nodes
        prev = self.nodes[self.roots[j]]['sizes'][-1]
        self.nodes[new_pop] = {'sizes':[{'t':t,'N':prev['N_top'], 'alpha':prev['alpha']}]}

        self.edges += [(new_pop, self.roots[i]), (new_pop, self.roots[j])]

        self.roots[j] = new_pop
        #del self.roots[i]
        self.roots[i] = None

        return t,i,j

    def _en(self, t,i,N):
        t,N = map(self.getfloat, [t,N])
        i = self.getpop(i)
        self.nodes[self.roots[i]]['sizes'].append({'t':t,'N':N,'alpha':None})
        return t,i,N

    def _eN(self, t, N):
        assert self.roots
        for i in self.roots:
             if self.roots[i] is not None:
                 self._en(t, i, N)
        return map(self.getfloat, (t,N))

    def _eg(self, t,i,alpha):
        if self.getfloat(alpha) == 0.0 and alpha[0] != "$":
            alpha = None
        else:
            alpha = self.getfloat(alpha)
        t,i = self.getfloat(t), self.getpop(i)
        self.nodes[self.roots[i]]['sizes'].append({'t':t,'alpha':alpha})
        return t,i,alpha

    def _eG(self, t,alpha):
        assert self.roots
        for i in self.roots:
            if self.roots[i] is not None:
                self._eg(t,i,alpha)
        return map(self.getfloat, (t,alpha))

    def _n(self, i,N):
        assert self.roots
        if self.events:
            raise IOError(("-n should be called before any demographic changes", kwargs['cmd']))
        assert not self.edges and len(self.nodes) == len(self.roots)

        i,N = self.getpop(i), self.getfloat(N)
        pop = self.roots[i]
        assert len(self.nodes[pop]['sizes']) == 1
        self.nodes[pop]['sizes'][0]['N'] = N

        return i,N

    def _g(self, i,alpha):
        assert self.roots
        if self.events:
            raise IOError(("-g,-G should be called before any demographic changes", kwargs['cmd']))
        assert not self.edges and len(self.nodes) == len(self.roots)
        i = self.getpop(i)
        pop = self.roots[i]
        assert len(self.nodes[pop]['sizes']) == 1
        if self.getfloat(alpha) == 0.0 and alpha[0] != "$":
            alpha = None
        else:
            alpha = self.getfloat(alpha)
        self.nodes[pop]['sizes'][0]['alpha'] = alpha

        return i,alpha

    def _G(self, rate):
        assert self.roots
        for i in self.roots:
            if self.roots[i] is not None:
                self._g(i, rate)
        return self.getfloat(rate),

    def _I(self, npop, *lins_per_pop):
        # -I should be called first, so everything should be empty
        assert all([not x for x in self.roots,self.events,self.edges,self.nodes])
        
        npop = self.getint(npop)
        lins_per_pop = map(self.getint,lins_per_pop)

        if len(lins_per_pop) != npop:
            raise IOError("Bad args for -I. Note continuous migration is not implemented.")

        for i in range(1,npop+1):
            self.nodes[i] = {'sizes':[{'t':0.0,'N':1.0,'alpha':None}],'lineages':lins_per_pop[i-1]}
            self.roots[i] = i
        return [npop] + lins_per_pop

    def get(self, var, vartype):
        if not isinstance(var,str):
            return var
        if var[0] == "$":
            ret = self.params_dict[var[1:]]
        else:
            ret = vartype(var)
        if isnan(ret):
            raise Exception("nan in params %s" % (str(self.params_dict)))
        return ret

    def getint(self, var):
        return self.get(var, int)

    def getfloat(self, var):
        return self.get(var, float)

    def to_nx(self):
        assert self.nodes
        self.roots = [r for _,r in self.roots.iteritems() if r is not None]

        if len(self.roots) != 1:
            raise IOError("Must have a single root population")

        node, = self.roots
        self.set_sizes(self.nodes[node], float('inf'))

        #cmd = sum([v for k,v in sorted(self.sorted_cmd.iteritems())],[])
        #cmd = " ".join(cmd)
        #print sorted(self.sorted_cmd.iteritems())
        cmd = " ".join(self.cmd_list)
        #print cmd

        ret = nx.DiGraph(self.edges, cmd=cmd, events=self.events)
        for v in self.nodes:
            ret.add_node(v, **(self.nodes[v]))
        return ret

    def set_sizes(self, node_data, end_time):
        # add 'model_func' to node_data, add information to node_data['sizes']
        sizes = node_data['sizes']
        # add a dummy epoch with the end time
        sizes.append({'t': end_time})

        # do some processing
        N, alpha = sizes[0]['N'], sizes[0]['alpha']
        pieces = []
        for i in range(len(sizes) - 1):
            sizes[i]['tau'] = tau = (sizes[i+1]['t'] - sizes[i]['t'])

            if 'N' not in sizes[i]:
                sizes[i]['N'] = N
            if 'alpha' not in sizes[i]:
                sizes[i]['alpha'] = alpha
            alpha = sizes[i]['alpha']
            N = sizes[i]['N']

            if alpha is not None:
                pieces.append(ExponentialHistory(tau=tau,growth_rate=alpha,N_bottom=N))
                N = pieces[-1].N_top
            else:
                pieces.append(ConstantHistory(tau=tau, N=N))

            sizes[i]['N_top'] = N

            if not all([sizes[i][x] >= 0.0 for x in 'tau','N','N_top']):
                raise IOError("Negative time or population size. (Were events specified in correct order?")
        sizes.pop() # remove the final dummy epoch

        assert len(pieces) > 0
        if len(pieces) == 0:
            node_data['model'] = pieces[0]
        else:
            node_data['model'] = PiecewiseHistory(pieces)



'''Simulate SFS from Demography. Call from demography.simulate_sfs instead.'''
def simulate_sfs(demo, num_sims, theta=None, seed=None, additionalParams=""):
    if any([(x in additionalParams) for x in "-t","-T","seed"]):
        raise IOError("additionalParams should not contain -t,-T,-seed,-seeds")

    lins_per_pop = [demo.n_lineages(l) for l in sorted(demo.leaves)]
    n = sum(lins_per_pop)
    pops_by_lin = []
    for pop in range(len(lins_per_pop)):
        for i in range(lins_per_pop[pop]):
            pops_by_lin.append(pop)
    assert len(pops_by_lin) == int(n)

    scrm_args = demo.ms_cmd
    if additionalParams:
        scrm_args = "%s %s" % (scrm_args, additionalParams)

    if seed is None:
        seed = random.randint(0,999999999)
    scrm_args = "%s --seed %s" % (scrm_args, str(seed))

    assert scrm_args.startswith("-I ")
    if not theta:
        scrm_args = "-T %s" % scrm_args
    else:
        scrm_args = "-t %f %s" % (theta, scrm_args)
    scrm_args = "%d %d %s" % (n, num_sims, scrm_args)

    #lines = sh.Command(os.environ["MSPATH"])(*ms_cmd.split(),_ok_code=[0,16,17,18])
    lines = sh.Command(os.environ["SCRM_PATH"])(*scrm_args.split())

    def f(x):
        if x == "//":
            f.i += 1
        return f.i
    f.i = 0
    runs = itertools.groupby((line.strip() for line in lines), f)
    next(runs)
    if theta:
        return [read_empirical_sfs(list(lines), len(lins_per_pop), pops_by_lin)
                for i,lines in runs]
    else:
        return [read_tree_lens(list(lines), len(lins_per_pop), pops_by_lin)
                for i, lines in runs]

def read_empirical_sfs(lines, num_pops, pops_by_lin):
    currCounts = Counter()
    n = len(pops_by_lin)

    assert lines[0] == "//"
    nss = int(lines[1].split(":")[1])
    if nss == 0:
        return currCounts
    # remove header
    lines = lines[3:]
    # remove trailing line if necessary
    if len(lines) == n+1:
        assert lines[n] == ''
        lines = lines[:-1]
    # number of lines == number of haplotypes
    assert len(lines) == n
    # columns are snps
    for column in range(len(lines[0])):
        dd = [0] * num_pops
        for i, line in enumerate(lines):
            dd[pops_by_lin[i]] += int(line[column])
        assert sum(dd) > 0
        currCounts[tuple(dd)] += 1
    return currCounts

def read_tree_lens(lines, num_pops, pops_by_lin):
    assert lines[0] == "//"
    return NewickSfs(lines[1], num_pops, pops_by_lin).sfs

class NewickSfs(newick.tree.TreeVisitor):
    def __init__(self, newick_str, num_pops, pops_by_lin):
        self.tree = newick.parse_tree(newick_str)
        self.sfs = Counter()
        self.num_pops = num_pops
        self.pops_by_lin = pops_by_lin

        self.tree.dfs_traverse(self)

    def pre_visit_edge(self,src,b,len,dst):
        dd = [0] * self.num_pops
        # get the # lineages in each pop below edge
        for leaf in dst.get_leaves_identifiers():
            dd[self.pops_by_lin[int(leaf)-1]] += 1
        # add length to the sfs entry. multiply by 2 cuz of ms format
        #self.sfs[tuple(dd)] += len * 2.0
        self.sfs[tuple(dd)] += len
