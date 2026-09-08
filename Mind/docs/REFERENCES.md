# Reference and reuse record

This is a compact record of prior source audits, not a new upstream verification
or a claim that external benchmark results transfer to Lumina.

| Reference | Pinned source and reuse |
| --- | --- |
| Tycho | [f68912a764372ead0a610db2e1c011d41ce5197e](https://github.com/NIMI-research/Tycho/tree/f68912a764372ead0a610db2e1c011d41ce5197e), Apache-2.0. Modified sandbox.py/wmlib_template.py portions support Mind/world_model.py. Local adaptation keeps bounded immutable inputs, no network/host mounts, deterministic run integrity and separately sourced observations. Game ontology and a permanent Builder organ were not transplanted. [License](cognition_minimal/TYCHO_LICENSE.txt), [adaptation](cognition_minimal/ISOLATED_MODEL_SOURCE_AUDIT.md). |
| MetaWorld / Decitron | [Wenge product description](https://www.wenge.com/site/69c9202de4b03b4745b93856), [author report 202608.00064](https://chinaxiv.org/abs/202608.00064). Prior audit used the product description and accessible author abstract; full paper/source was not available then. Conceptual reference for structured state, conditions, actors and uncertainty. No claimed code transplant, weights or license. Not Farama Meta-World. |
| AVO | [paper v1](https://arxiv.org/html/2603.24517v1). Reference for bounded action, evaluation feedback and revision. No runtime code copied and no claim of reproducing its performance or training method. |
| CRITIC | [ProphetNet/CRITIC 5cf70eb41cdaa1d8faa3e1265d95ee5792d49a53](https://github.com/microsoft/ProphetNet/tree/5cf70eb41cdaa1d8faa3e1265d95ee5792d49a53/CRITIC), MIT. Research reference for one model investigating with external feedback; no copied parser, oracle or new reviewer Agent. |
| Codex | [553df1c691fe8bf7747e50da22f1342984495ae0](https://github.com/openai/codex/tree/553df1c691fe8bf7747e50da22f1342984495ae0), Apache-2.0. Prior code audit informed separation of runtime loop, durable evidence and context; no code transplant claimed. |
| mini-swe-agent | [04d809ceab9df28f9adaed044884180159172930](https://github.com/SWE-agent/mini-swe-agent/tree/04d809ceab9df28f9adaed044884180159172930), MIT. Loop simplicity reference; no source copied. |
| OpenHands SDK | [v1.45.0](https://github.com/OpenHands/software-agent-sdk/tree/v1.45.0), MIT. Prior audit of state/context/step ownership; no code transplant claimed. |

Lumina's authority remains its own: Mind judges direction, Execution acts,
Nervous transports events, and optional Builder analysis returns attributed
results. External designs do not certify semantic correctness or authorize
extra roles, gates or runtime model migrations.