# Lingren

<p align="center">
  <img src="branding/logo-h-manual.svg" width="520" alt="Lingren horizontal lockup">
</p>

<p align="center">A standalone singing voice synthesis (SVS) API service, called by Yeban.</p>

One FastAPI codebase, three deployments: local macOS, remote Colab, Docker on Linux GPU.

- Default model: SoulX-Singer (strict score-clock follower, Apache-2.0)
- Quality option: VocalRender (48kHz, Apache-2.0, requires prompt audio; timing is model-decided, the service receipt reports measured onsets for placement)
- Job API: `parts × takes` (multi-take stacking for thickness, multi-part harmony); a batch difference gate guarantees takes are never identical
- Yeban side: two MCP tools (`synthesize_singing`, `svs_job`); synthesized output is an ordinary recorded clip, imported through the existing audio-clip mechanism

中文版：[README.zh-CN.md](README.zh-CN.md).

## Documents

- Design: [RESEARCH-AND-DESIGN.md](RESEARCH-AND-DESIGN.md)
- Phase 0 measurements: [phase0/REPORT.md](phase0/REPORT.md) (official demos of both models on Colab L4; model selection confirmed)
- Phase 1: [phase1/REPORT.md](phase1/REPORT.md) (patch verification + service skeleton + SoulX adapter + job API)
- Phase 2: [phase2/REPORT.md](phase2/REPORT.md) (VocalRender adapter wiring + Yeban tool bodies + long-phrase re-verification + prompt pipeline)
- Brand manual: [branding/brand-manual.html](branding/brand-manual.html)

## Status

Phases 0, 1 and 2 are complete and measured. Remaining: Mac MPS measurement (trigger: if SoulX MPS RTF > 2, start the MLX phase-2 port), named tunnel (login account and zone to be decided), Docker image build on a Linux GPU machine, Yeban tools ↔ live service end-to-end test.

## License

This repository's own content is MIT licensed, see [LICENSE](LICENSE). Model code and weights follow their own licenses: SoulX-Singer and VocalRender are both Apache-2.0, see §10 of the design document.
