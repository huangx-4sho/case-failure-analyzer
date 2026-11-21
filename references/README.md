# Reference index

## Commands

- [CLI workflows](cli.md) covers audit modes, saved-result inspection, comparisons, validation, and rendering.

## Attribution and review

- [Root-cause taxonomy](taxonomy.md) covers execution stages, the four peer causes and [eight agent capability dimensions](taxonomy.md#agent-capability-dimensions).
- [Attribution protocol](attribution-protocol.md) defines the decision order and evidence requirements.
- [Existing-evidence review](evidence-review.md) explains how to trace missing outputs, inspect recorded verification, review causal claims, and assess whether guidance applies across tasks.
- [Agent mechanism tags](agent-mechanisms.md) lists trial-level tags for reviewed capability diagnoses.
- [Skill prescription policy](skill-prescription-policy.md) defines eligibility for reusable guidance and what that guidance should contain.

## Output contracts

- [Evidence schema](evidence-schema.md) defines `evidence.json`, `analysis.json`, and valid evidence references.
- [Report schema](report-schema.md) specifies verdict cards, single-trial reports, and batch indexes.

## Scientific software

The [error family registry](error-families.json) supplies the machine-readable matching rules. The software notes describe the evidence needed to interpret matches.

| Domain | References |
| --- | --- |
| Electronic structure and semiempirical methods | [CP2K](software/cp2k.md), [Quantum ESPRESSO](software/quantum-espresso.md), [VASP and ABACUS](software/vasp-abacus.md), [ORCA, Gaussian, and PySCF](software/orca-gaussian-pyscf.md), [xTB and CREST](software/xtb.md) |
| Molecular dynamics and interatomic potentials | [LAMMPS](software/lammps.md), [GROMACS, AMBER, and OpenMM](software/gromacs-amber-openmm.md), [ML interatomic potentials](software/mlip.md) |
| Workflows, cheminformatics, and numerical solvers | [ASE and pymatgen](software/ase.md), [RDKit](software/rdkit.md), [General scientific software](software/generic-scientific.md) |
