# Inference providers versus a self-hosted stack

## Decision summary

For this product, use the provider-neutral STT, reasoning, and TTS contracts to support a hybrid
deployment. A hosted API is the lowest-risk route while volume is uncertain and quality is still
being evaluated. A dedicated or self-hosted reasoning pool becomes attractive when traffic is
steady enough to keep GPUs busy, data-residency or model-control requirements justify operations,
and the team can prove its own p95 latency and failover. Speech can move independently: retaining
Speaches for private/local audio while using a hosted reasoning model is a valid production shape.

The original market/infrastructure analysis is a planning snapshot dated **2026-09-02**. The
Deepgram TTS and hosted Whisper STT comparison and worked speech costs were checked against official
documentation on **2026-09-04**; this does not reprice the other providers below. Prices and catalog
availability change frequently; verify them during procurement. Figures are USD public list prices,
exclude negotiated discounts and tax, and are not performance guarantees.

## The market in practical categories

| Category | Representative choices | Charging model | Best fit | Main trade-off |
|---|---|---|---|---|
| Frontier model APIs | OpenAI, Anthropic, Google Gemini | Input, cached-input, and output tokens; audio may use tokens or time | Fast launch, strongest managed models, bursty demand | Provider dependency, variable queueing, external processing |
| Cloud model platforms | AWS Bedrock, Azure AI Foundry, Google Vertex AI | Per token plus optional priority, reserved, or provisioned capacity | Enterprise identity, region, governance, multi-model procurement | More configuration; catalog and pricing vary by region |
| Inference clouds | Fireworks, Together, Groq, Cerebras, Baseten | Serverless tokens or dedicated accelerator time | Open models without running the serving stack; fast experimentation | Serverless has quotas/catalog churn; dedicated capacity still needs sizing |
| Hosted speech APIs | OpenAI Whisper STT; Deepgram Aura TTS | Transcribed audio minutes or synthesized text characters | Evaluate speech independently of reasoning; avoid dedicated speech GPU capacity | Model-specific protocols, external processing, quotas, and speech quality/latency admission |
| Managed dedicated endpoints | Fireworks/Together/Baseten and cloud GPU services | GPU-second/hour or committed replicas | Predictable capacity, custom weights, fewer platform duties | Idle cost and vendor-specific deployment controls |
| Self-hosted inference | vLLM, SGLang, TensorRT-LLM, llama.cpp; Speaches/faster-whisper/Kokoro | Hardware lease or purchase, power, storage, network, people | Data control, exact versions, stable high utilization, custom models | Capacity planning, upgrades, on-call, failover, security patching |

[OpenAI](https://developers.openai.com/api/docs/models/gpt-5),
[Anthropic](https://docs.anthropic.com/en/docs/about-claude/pricing), and
[Gemini](https://ai.google.dev/gemini-api/docs/pricing) publish token-based pricing with discounted
cached input. [Bedrock](https://aws.amazon.com/bedrock/pricing/) exposes multiple providers and
standard, flex, priority, and reserved modes. Open-model inference clouds similarly offer serverless
and dedicated tiers; for example, [Fireworks](https://docs.fireworks.ai/serverless/overview) states
that serverless is per-token shared capacity while on-demand is private GPU capacity.

### Speech options: Deepgram TTS and Whisper STT

These are additional evaluation candidates, not replacements for the selected local deployment or
a declaration of a universal "best" model. Keep STT and TTS selection independent of reasoning.

| Option | Pricing basis checked 2026-09-04 | Fit and comparison boundary |
|---|---|---|
| OpenAI hosted `whisper-1` STT | $0.006 per audio minute | Managed transcription of completed candidate turns; evaluate upload-to-final latency and transcript accuracy |
| Deepgram Aura-2 TTS | Pay-as-you-go $0.030 per 1,000 input characters | Candidate for interviewer speech; compare pronunciation, naturalness and first-audio latency |
| Deepgram Aura-1 TTS | Pay-as-you-go $0.015 per 1,000 input characters | Lower-priced alternative to include in the same listening and latency evaluation |
| Self-hosted Speaches speech stack | GPU/CPU capacity, operations and infrastructure; no hosted per-minute/per-character API fee | Existing benchmark baseline: `faster-distil-whisper-small.en` STT and Kokoro TTS, not the hosted `whisper-1` service |

Rates come from the [official OpenAI Whisper model documentation](https://developers.openai.com/api/docs/models/whisper-1)
and [Deepgram pricing](https://deepgram.com/pricing). Deepgram also lists newer Flux TTS; the worked
comparison uses Aura's standard rates rather than temporary promotional credits or free periods.
Recheck model availability, plan/region limits and data-use terms before procurement.

Deepgram documents a [TTS WebSocket interface](https://developers.deepgram.com/reference/text-to-speech/speak-streaming).
Its streaming capability does not make our current buffered synthesis route streaming: a dedicated
adapter and a verified playback path would be needed. Its model-improvement opt-out can affect
pricing, so confirm a privacy-compatible quote rather than assuming the base rate is admissible.
Hosted Whisper's file-transcription option likewise does not replace the current live VAD socket
merely by changing a base URL. No Deepgram adapter or live hosted speech benchmark is included in
this documentation update.

The local distilled English Whisper model is a different model/configuration from hosted Whisper.
Do not transfer accuracy, language support, latency or concurrency results between them. Use the
[retained local report](reports/LOAD_TEST_REPORT_VLLM_2026-09-03.md) only for its stated configuration;
provider claims and list prices are not measured project results.

## Cost comparison

### A reproducible interview workload

Use workload units rather than a single vendor headline price. One illustrative 45-minute interview
with 12 candidate turns might consume:

- reasoning: 36,000 uncached input tokens, 12,000 cached-prefix tokens, and 3,000 output tokens;
- STT: 25 minutes of candidate audio;
- TTS: 5 minutes of interviewer audio; for character-priced APIs, assume 150 spoken words/minute
  and 6 characters/word including spaces, giving **4,500 input characters**;
- 12 live answer analyses plus intake planning and final assessment.

The numbers are deliberately conservative and must be replaced with telemetry. If cached input is
one tenth the uncached rate, reasoning at `$0.50/M` input and `$3.00/M` output is about **$0.028 per
interview** before speech, network, retries, and platform fees. At `$3/M` input and `$15/M` output it
is about **$0.157**.
Token APIs are consequently difficult to beat at low or irregular volume, especially while a
self-hosted GPU would sit idle.

Public examples recorded at the snapshot date show the breadth of the market rather than an apples-to-apples quality
ranking: [Together](https://www.together.ai/pricing) lists open models from fractions of a dollar to
several dollars per million tokens, and [Fireworks serverless pricing](https://docs.fireworks.ai/serverless/pricing)
does the same while discounting batch inference by 50%. Different models produce materially
different interview quality; a cheaper token is not equivalent output.

### Worked hosted speech costs

Apply the cited Whisper and Deepgram rates to the workload above:

| Component | Calculation | Estimated cost per interview |
|---|---|---:|
| Hosted Whisper STT | 25 audio minutes x $0.006/minute | $0.1500 |
| Aura-1 TTS alternative | 4,500 characters / 1,000 x $0.015 | $0.0675 |
| Aura-2 TTS alternative | 4,500 characters / 1,000 x $0.030 | $0.1350 |

Use one TTS alternative, not both. Whisper + Aura-1 gives **$0.2175 for speech**, or approximately
**$0.25-$0.37 including reasoning**. Whisper + Aura-2 gives **$0.2850 for speech**, or approximately
**$0.31-$0.44 including reasoning**. The ranges use the two illustrative reasoning tariffs above;
they are not quotes for named reasoning models or complete platform costs.

These are calculations, not observed bills. Count the actual audio submitted and characters sent,
including billed silence, retries, re-synthesis for repeats/clarifications, and any request-rounding
rules that apply. Five minutes of TTS is only a planning assumption; speech speed, language and text
length change the character total. Credits, discounts, privacy-related surcharges, storage, network,
application hosting, support and redundancy are excluded. Recalculate from production telemetry.

For the self-hosted alternative, allocate measured infrastructure and operations costs to admitted,
successfully completed interviews. An absent speech API fee does not make local STT/TTS free.

### Dedicated/self-hosted break-even

For rented capacity, calculate:

```text
monthly_capacity_cost = hourly_accelerator_price * 730 * required_replicas
effective_cost_per_interview = monthly_capacity_cost / completed_interviews
                               + operations + storage + network + backup
break_even_interviews = monthly_capacity_cost / hosted_variable_cost_per_interview
```

This last equation is a compute-only simplification. Compare the same capabilities on both sides:
if only reasoning moves to a GPU, hosted Whisper/Aura charges remain and are not avoided costs.
If all three capabilities move, prove that the priced hardware serves reasoning, STT and TTS at
the required latency and quality. A fuller calculation divides incremental monthly fixed costs by
`avoided_hosted_cost_per_interview - self_hosted_variable_cost_per_interview`; a non-positive
denominator means no cost break-even under those assumptions.

As a transparent reference, Fireworks listed an H100/H200 on-demand deployment at **$8/hour** from
2026-09-01, or about **$5,840/month** if left on continuously, before redundancy. At a hypothetical
hosted variable cost of $0.20/interview, compute-only break-even would be about 29,200 interviews per
month—and a production pair for failover doubles the idle floor. This is not a quote for owning a
server; it demonstrates why utilization dominates self-host economics. See the current
[Fireworks pricing page](https://fireworks.ai/pricing) before using the value in a budget.

Owned hardware can lower the amortized accelerator rate, but the comparison must include three- to
five-year depreciation, power and cooling, spare capacity, networking, rack/colo, engineering and
on-call time, replacement risk, and the opportunity cost of reserved headroom. Self-hosting often
wins first on control or residency, not price.

## Latency and throughput

For a voice interview, average tokens/second alone is not the user experience. Track:

- VAD endpoint delay and STT finalization time;
- reasoning queue time, time to first token (TTFT), inter-token latency, and total generation time;
- TTS time to first audio (TTFA) and synthesis real-time factor;
- speech-end to interviewer-audio-start p50/p95/p99;
- admitted concurrent interviews, queue age, and deadline/error rate at saturation.

Hosted serverless offers elastic burst capacity and no idle GPU, but tail latency and rate limits are
outside the application's control. Priority/provisioned tiers trade money or commitment for better
predictability. Dedicated endpoints remove multi-tenant queue variability but still need at least
two failure domains for production continuity.

For the speech shortlist, run the same accented/noisy, technical-vocabulary candidate recordings
through hosted Whisper and the exact self-hosted STT model. Compare word/critical-term errors,
empty or hallucinated transcripts, finalization p95 and successful audio-seconds/second. For Aura
versus Kokoro, reuse interviewer text and measure first playable audio, full synthesis latency,
real-time factor and pronunciation/listening quality. Repeat at target concurrency with actual
account quotas. Streaming-provider TTFA and our buffered full-response latency are different
metrics and must not be presented as interchangeable benchmarks.

Self-hosting exposes every optimization knob. The repository benchmark harness targets vLLM so that
continuous batching, paged KV memory, prefix caching, provider queue time, and per-request timing
are measured instead of approximated by application-level fallbacks. SGLang and TensorRT-LLM remain
production candidates. `llama.cpp` remains a useful edge and workstation option, but its former
single-slot project results are retired. Choice still requires a workload-matched test; no engine
wins every prompt length, model, accelerator, and latency objective.

## Emerging shifts worth watching

| Shift | Potential benefit here | Adoption condition |
|---|---|---|
| Prefix/KV cache reuse | Reuses the long, stable interview policy/rubric prefix; reduces repeat prefill work | Stable prompt prefixes, session affinity, cache-hit telemetry, privacy-safe eviction |
| Disaggregated prefill/decode | Independently scales long resume/rubric prefill and short answer-analysis decode | Multi-GPU scale and fast KV transport; not justified for this single-GPU dev profile |
| Speculative decoding and multi-token prediction | Can reduce short structured-response generation time | Draft acceptance and output validity must beat the extra VRAM/complexity |
| FP8/FP4 and improved quantization | More model or concurrency per accelerator | Re-run question-quality, schema-validity, and fairness evaluations after quantization |
| Mixture-of-experts and smaller specialist models | Lower active compute for routing/turn classification | Keep a stronger fallback and measure difficult/gibberish/clarification cases |
| Native speech-to-speech models | Lower turn latency and more natural prosody | Must retain transcript provenance, exact policy control, auditability, and safe text fallback |
| Inference-aware routing | Routes simple turn classification to a small model and hard cases to a stronger one | Calibrated confidence, bounded escalation, region/privacy controls, no score drift |

[NVIDIA Dynamo](https://docs.nvidia.com/dynamo/dev/kubernetes/disaggregated-serving/overview)
documents separate prefill/decode pools and explicitly warns that smaller models and short prompts
may be faster with the simpler aggregated layout. Its
[KV-aware routing](https://docs.nvidia.com/dynamo/kubernetes/kv-aware-routing/using-the-dynamo-frontend)
targets cache locality. `llama.cpp` now provides a workload benchmark for comparing baseline and
speculative decoding ([SPEED-Bench](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/bench/speed-bench/README.md)).
These are promising mechanisms, not automatic production wins.

## Recommended procurement sequence

1. Keep provider-neutral application contracts with provider-specific adapter boundaries, and build
   a 50-100 transcript quality set. An OpenAI-compatible wire shape is not the domain contract.
2. Compare at least one frontier hosted model, one low-cost hosted open model, and the local vLLM
   profile on question quality, turn disposition, schema validity, latency, and cost.
   Independently compare hosted Whisper with the local STT baseline, and Deepgram Aura with Kokoro;
   carry the selected speech costs into the full interview budget.
3. Start production with hosted or managed dedicated inference unless residency forbids it.
4. Introduce self-hosted Speaches independently if audio locality is the stronger requirement.
5. Move reasoning to a self-hosted production engine only after a 30-day demand trace shows enough
   utilization and a two-replica load/failure test meets the SLO.
6. Retain a contract-compatible hosted failover unless policy prohibits external processing.

The unit of comparison is **cost per successfully completed, reviewable interview at the target
p95**, not cost per token or peak laboratory tokens/second.
