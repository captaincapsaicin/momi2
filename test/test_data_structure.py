import json
import os
import momi
from momi.data.compressed_counts import CompressedAlleleCounts
from io import StringIO

from demo_utils import simple_five_pop_demo
import autograd.numpy as np
from collections import Counter


def test_combine_loci():
    demo = simple_five_pop_demo(n_lins=(10, 10, 10, 10, 10))
    demo.demo_hist = demo.demo_hist.rescaled()
    n_loci = 1000

    # data = momi.simulate_ms(scrm_path, demo,
    #                        num_loci=n_loci, mut_rate=.1)
    #raw_ms = momi.simulate_ms(scrm_path, demo.demo_hist,
    #                          sampled_pops=demo.pops, sampled_n=demo.n,
    #                          num_loci=n_loci, mut_rate=.1,
    #                          raw_output=True)
    num_bases = 1000
    mu = .1
    data = demo.demo_hist.simulate_data(
        demo.pops, demo.n,
        mutation_rate=mu/num_bases,
        recombination_rate=0,
        length=num_bases,
        num_replicates=n_loci)

    #data = momi.parse_ms.seg_sites_from_ms(raw_ms, demo.pops)
    assert data.n_loci == n_loci

    data.sfs.combine_loci()
    # TODO check the combined SFS is correct?


def test_readwrite_segsites_parse_ms_equal():
    demo = simple_five_pop_demo(n_lins=(10, 10, 10, 10, 10))
    demo.demo_hist = demo.demo_hist.rescaled()
    n_loci = 1000

    assert False
    # TODO this test is heavily dependent on reading ms?

    #raw_ms = momi.simulate_ms(scrm_path, demo.demo_hist,
    #                          sampled_pops=demo.pops, sampled_n=demo.n,
    #                          num_loci=n_loci, mut_rate=.1,
    #                          raw_output=True)
    data = momi.parse_ms.seg_sites_from_ms(raw_ms, demo.pops)


    assert data.n_loci == n_loci

    check_readwrite_data(data)
    check_readwrite_ascertain(data, [True, True, False, True, False])

    raw_ms.seek(0)
    sfs_dict = Counter()
    curr_lines = None
    ind2pop = sum([[i] * n for i, n in enumerate(demo.n)], [])

    def update_sfs_dict():
        assert len(curr_lines) == np.sum(demo.n)
        n_snps = len(curr_lines[0])
        config_array = np.zeros((n_snps, len(demo.pops), 2), dtype=int)
        for i, snp in enumerate(zip(*curr_lines)):
            snp = map(int, snp)
            for pop, allele in zip(ind2pop, snp):
                config_array[i, pop, allele] += 1
        for config in config_array:
            config = tuple(map(tuple, config))
            sfs_dict[config] += 1
    for line in raw_ms:
        line = line.strip()
        if line == '':
            continue
        elif line[0] in ('0', '1'):
            if curr_lines is not None:
                curr_lines.append(line)
        elif line == "//":
            if curr_lines is not None and len(curr_lines) != 0:
                update_sfs_dict()
            curr_lines = []
    update_sfs_dict()
    sfs_dict = dict(sfs_dict)
    assert sfs_dict == data.sfs.to_dict()


def check_readwrite_data(data):
    strio = StringIO()
    momi.write_seg_sites(strio, data)

    strio = StringIO(strio.getvalue())
    data2 = momi.read_seg_sites(strio)

    assert data == data2
    assert data.sfs == data2.sfs


def check_readwrite_ascertain(data, ascertainment_pop):
    #assert not all(ascertainment_pop)
    ascertainment_pop = np.array(ascertainment_pop)
    newdata = momi.seg_site_configs(data.sampled_pops,
                                    ((conf
                                      for conf in loc
                                      if not np.any(np.sum(conf[ascertainment_pop, :], axis=0) == 0)
                                      ) for loc in data),
                                    ascertainment_pop=ascertainment_pop)

    assert np.all(newdata.ascertainment_pop == ascertainment_pop)
    check_readwrite_data(newdata)

    # def _copy(self, ascertainment_pop=None):
    #     if ascertainment_pop is None:
    #         ascertainment_pop = self.ascertainment_pop
    #     return seg_site_configs(self.sampled_pops, (self[loc] for loc in range(self.n_loci)),
    #                             ascertainment_pop=ascertainment_pop)



def test_load_data():
    data_path = "test_data.json"
    if not os.path.exists(data_path):
        with open(data_path, "w") as data_f:
            vcf_path = "test_vcf.vcf"
            with open(vcf_path) as vcf:
                data = momi.SnpAlleleCounts.read_vcf(
                    vcf_path, ind2pop={
                        f"msp_{i}": f"Pop{i%3}"
                        for i in range(1,7)})
                data.dump(data_f)


    with open(data_path) as f:
        data = momi.SnpAlleleCounts.load(f)

    with open(data_path) as f:
        info = json.load(f)

        chrom_pos_config_key = "(chrom_id,position,config_id)"
        chrom_ids, positions, config_ids = zip(
            *info[chrom_pos_config_key])
        del info[chrom_pos_config_key]

        compressed_counts = CompressedAlleleCounts(
            np.array(info["configs"], dtype=int),
            np.array(config_ids, dtype=int))
        del info["configs"]

        data2 =  momi.SnpAlleleCounts(
            chrom_ids, positions, compressed_counts, **info)

    assert data.sfs == data2.sfs
