#!/usr/bin/env bash
# Record the feature tour and cut it to size: docs/demo/feature-tour.mp4 (just under 90 seconds;
# TOUR_SECONDS changes the target, TOUR_PACE how long each banner is held).
# The recording waits for the real language model, whose speed varies, so the dull part of each
# wait is cut out afterwards (the on-screen label still states the measured time).
#   e2e/demo/tour-stack.sh up                    # once; see that file for what it starts
#   pip install imageio-ffmpeg                   # a static ffmpeg for the cutting step
#   e2e/demo/make-feature-tour.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
e2e/demo/tour-stack.sh reset
rm -rf docs/demo/tmp-tour
(cd e2e && DEMO_TOUR=1 E2E_BASE_URL=http://localhost:8104 E2E_TOKEN=tour-token \
  npx playwright test -c playwright.demo.config.ts demo/feature-tour.spec.ts)
python3 e2e/demo/retime.py docs/demo/tmp-tour/raw.webm docs/demo/tmp-tour/waits.json docs/demo/feature-tour.mp4 "${TOUR_SECONDS:-89.5}"
rm -rf docs/demo/tmp-tour
