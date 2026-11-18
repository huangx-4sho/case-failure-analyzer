# CompChemWorld validation

[简体中文](../zh/compchemworld-validation.md) · [Project home](../../README.md)

CFA audited 196 saved trials in two batches of 98. Of these, 62 passed and 8 were pending independent review. All 68 scored failures and 58 errored trials had a recorded runtime interruption, a scoring rejection, or both. The audit used the original task results and saved evidence without rerunning the simulations or verifiers.

| Finding | Trials |
|---|---:|
| Runtime interruption | 76 |
| Scoring rejection | 76 |
| Both events in the same trial | 26 |
| Distinct failed or errored trials | 126 |

The rules selected a candidate cause for 95 of the 126 trials: infrastructure in 75, an agent decision in 11, and a verifier problem in 9. They selected no task-design candidate. Responsibility remained open in 31 trials. In 28 trials, the first scoring diagnostic also matched a unique location in the scorer source. A matched line tells the reviewer which condition rejected the input. Assigning responsibility requires comparing that condition with the public task and the submitted artifact.

## Reading an individual result

- In task037, the image build restored apparently missing trajectories from shards. The verifier rejected a text explanation because it expected a literal boolean. A review traced the rejection to a type requirement absent from the public task.
- In task044, the final submission contained `null` in an atom-count field that required an integer. The generator had tried to derive the count from missing force data even though the coordinates contained 304 atoms. The CP2K convergence error belonged to the historical records the agent was analyzing.
- Fifteen trials acquired an infrastructure candidate when the audit identified a terminal model-service quota error. Their original scores were preserved. In task103, the model request stopped at HTTP 429 and the verifier subsequently rejected missing outputs. Task087 illustrates a different distinction: its score of 0.30 measures record completeness, and the scientific result still awaits review.

## Self-checks across independent tasks

In task044, the agent's self-check missed a nested integer constraint. In task049, a different self-check accepted extra fields in a nested object. Both checks ran. Their sources, recorded results, public criteria and scoring rejections supported a cross-task finding about coverage of nested output contracts.

Reviews also supported the self-check decisions in tasks 093 and 170. Task143 remained a candidate because its public stopping criterion specified maximum atomic force without spelling out the norm. These conclusions apply to the decisions reviewed in these trials. [Capability dimensions](taxonomy.md#agent-capability-dimensions) · [How to review a decision](evidence-review.md#failure-location-and-self-check-evidence)
