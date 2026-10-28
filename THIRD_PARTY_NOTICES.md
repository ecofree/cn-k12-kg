# Sources, attribution and distribution

This file implements the licensing choices already stated in README.md. It does not relicense third-party material.

## Project materials

- Project-authored code: MIT; see LICENSE.
- Original educational explanations, questions, diagrams and activities: CC BY 4.0; attribution name: **cn-k12-kg**. See LICENSE-DATA-CC-BY-4.0.txt. The full text was obtained from the [SPDX official license list](https://github.com/spdx/license-list-data/blob/main/text/CC-BY-4.0.txt), SHA-256 d557539df68e771cc1eedcc91d13f70fca930e508d11eedcafa4b15db49e3744.
- Quoted curriculum-standard excerpts are attributed to the sources and locators in data/standards.json and data/curriculum_comparisons/. They are not represented as original project-authored text.

## KaTeX 0.16.11

web/static/vendor/katex contains unmodified JavaScript, CSS and fonts from the official [KaTeX 0.16.11 npm release](https://registry.npmjs.org/katex/-/katex-0.16.11.tgz). All 63 previously bundled files were compared byte-for-byte with that release. The release's license is retained at web/static/vendor/katex/LICENSE.

Copyright (c) 2013–2020 Khan Academy and other contributors. MIT License, as supplied in that release.

## TAL-SCQ5K-CN

The optional challenge collection at data/challenge/tal.json is a format conversion of TAL-SCQ5K-CN, created by TAL Education Group. The [original dataset repository](https://github.com/math-eval/TAL-SCQ5K) and [publisher's dataset card](https://huggingface.co/datasets/math-eval/TAL-SCQ5K) state MIT licensing. Source identifiers and the challenge-only restriction are retained. These records are not project-authored course questions and never count as diagnostic evidence merely because they exist in the package.

## Unbundled external material

data/questions_ext.json and data/external/ are optional local inputs and are not part of the ordinary source delivery. Source availability, permission to distribute, curricular alignment and answer evidence are separate fields. A public URL alone does not establish any of them.

## Development dependencies

JSON Schema checks use the pinned package in requirements-dev.txt. This is a build/test dependency; the local learning server remains a Python standard-library application. Its installed packages retain upstream licenses and are not vendored into the source bundle.
