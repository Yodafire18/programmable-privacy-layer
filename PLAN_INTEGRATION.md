# Integrating the vision into the technical plan

Sources: `VISION.md` (the "Original notes" are treated as the source of truth) and `~/.claude/plans/witty-wibbling-swing.md`. I also looked at the code already in the repo (`privacy_filter/`, `eval/`). Neither the plan nor the vision was edited.

**Bottom line:** The pipeline and eval harness stay. The change is that the rules (what to blur, what to allow) move out of Python and into a small **profile file**. A small **MCP server** writes and tests those profiles, and the Quest is only a front end to it. Everything described below as a format, tool name or file name is a **proposal**, not something the vision specifies.

What the vision commits to, and so all this document tries to make real:
1. Natural language becomes a custom profile, through an MCP server on the computer that runs the logic.
2. The profile is tested with synthetic data.
3. The user tries it and tunes it.
4. The profile is used from glasses "or anything".
5. A doctor on a Meta Quest is the example author.

The vision's examples: blur documents, X-rays and children's faces; blur faces but let two peace signs unblur (consent); filter bystanders and license plates (vlogger); cover drink brands they don't like (streamer).

---

## 1. How the existing plan maps onto the vision

**Already fits (keep as is):**
- **The pipeline is a pure function of frames plus config.** `config.py` already loads defaults, a YAML overlay and dotted overrides. A profile can therefore *compile into* that config plus a list of targets, so the core needs no rewrite.
- **The source abstraction** (`webcam`, `file`, `folder`, `rtmp`/Mentra, later Ray-Ban over WebSocket) is exactly what "use that profile from glasses or anything" needs. The profile lives on the Mac, and any device is just a source feeding the Mac pipeline.
- **The consent engine** (two peace signs, hold timer, memory, revoke) becomes the implementation of one allow rule. The vision's own example *is* this feature.
- **The eval harness** already exists: `python -m eval synth` composites a still image into moving-camera clips, `metrics.py` checks blur coverage per labeled region, and there are stage caching and a run/report flow. That is most of the "test it with synthetic data" step already.
- **Smoothness work** (timeline buffer, hold/backfill, fade, fail-closed) applies to every target and doesn't change.

**Has to change:**
- **Hard-coded categories become profile-driven targets.**
  - Planned: `sensitive.py`, fixed YOLO-World prompts, `config.yaml` category toggles.
  - New: a profile lists targets, and a small **target registry** maps each target to a detector.
- **"Every face is blurred" becomes a profile setting.**
  - The doctor's profile ("blur ... children's faces") implies adult faces are *not* blurred. So the face default (blur or visible) and the face blur conditions must come from the profile.
  - Today `consent.py` assumes blur by default.
- **Metrics must read the profile.** `metrics.expected()` currently assumes "blurred unless consented or favorite". It needs to derive expected visibility from the active profile. Otherwise the leak gate is wrong for the doctor profile.
- **Cache key.** The perception cache key must include the profile's detector settings (targets and prompts). Otherwise a tuned profile silently reuses stale detections.
- **Live app.** `app.py` needs to load the active profile and reload it when it changes.

---

## 2. Minimal profile format (proposal)

One YAML file per profile in `profiles/<name>.yaml`. The original sentence is kept in the file, so "what you said" and "what it does" sit side by side.

```yaml
name: doctor
said: "Blur documents, X-rays and children's faces."   # the user's words, verbatim

faces:
  default: visible          # visible | blur
  blur_if: [child]          # optional face conditions; v1 supports only: child

allow:                      # only meaningful when a face would otherwise be blurred
  - consent_gesture         # two peace signs (existing ConsentEngine; thumbs-down revokes)
  # - favorites             # existing planned feature, if the team keeps it

blur:                       # objects, always blurred, even on visible people (as in the plan)
  - target: document
  - target: xray
    prompts: ["x-ray film", "radiograph"]   # optional override of the registry's prompts
    min_score: 0.15                         # tuning knob

tuning:                     # optional dotted overrides into the existing config
  timeline.buffer_s: 1.5
```

The other example profiles fit in a few lines each:
- **Consent example:** `faces.default: blur`, `allow: [consent_gesture]`, `blur: []`.
- **Vlogger:** `faces.default: blur`, `allow: [consent_gesture]`, `blur: [license_plate]`.
- **Streamer:** `faces.default: visible`, `blur: [{target: drink_brand, brands: ["<brand>"]}]`.

**Precedence (proposal, matching the plan's existing rule):**
1. Object blur always applies.
2. For faces: a `blur_if` match means blur.
3. Otherwise, an `allow` match means visible.
4. Otherwise, `faces.default` applies.

Whether a child can unblur themselves with the consent gesture is a product decision for the team. The proposal above says no, because blur beats allow.

**Target registry: what the planned detectors can handle, honestly**

| Target | Detector | Expected quality |
|---|---|---|
| `face` | SCRFD (already built) | Good. Small and distant faces are fine. |
| `consent_gesture` (allow) | MediaPipe `Victory` ×2 + association (already built) | Good in controlled conditions. Already tested. |
| `license_plate` | YOLO-World text prompt (planned) | Fair to good on clear, nearby plates. Weaker when small or angled. |
| `document` | YOLO-World "document / paper" (planned) | Fair. A sheet of paper is generic, so expect some false blurs on blank paper and screens. |
| `card`, `id` | YOLO-World + optional OCR/Luhn (planned) | Fair. OCR helps when the numbers are readable. |
| `child` (face condition) | InsightFace `genderage.onnx`. It already ships in both `buffalo_s` and `buffalo_l` on this machine; the plan just doesn't enable the module. | **Weak to fair.** Age regression is noisy, and worst on children, profiles and low resolution. To fail closed, blur when predicted age < about 21, not < 18, and accept that some young adults get blurred. Measure on generated faces before promising anything. |
| `xray` | Open-vocabulary detection with a text prompt (YOLO-World; OWLv2 or Grounding DINO if YOLO-World fails). Fallback: run CLIP zero-shot on `document`/`screen` boxes ("an x-ray" vs. "a document"). | **Weak.** X-rays are rare in open-vocabulary detector training data. Expect it to work on large, frontal films or X-ray images on a screen in a staged set. Don't claim it works in the wild. |
| `drink_brand` | YOLO-World "can / bottle / cup", then on each crop either OCR the label for the brand name or run CLIP zero-shot "a can of <brand>". | **Fair** for big, frontal, well-known logos. Poor for small or partly hidden labels. |
| `custom` (free-text prompt) | YOLO-World with the user's prompt | Unknown per prompt. This is exactly what the synthetic test is for. |

Keep the registry to these targets. The MCP exposes it so the LLM can only pick targets that exist. Anything else becomes `custom` with a prompt and gets tested.

---

## 3. Minimal MCP server (proposal)

**Shape**
- One file, `mcp_server.py`, built with the official **Python MCP SDK** (`mcp` package, `FastMCP`). It runs on the Mac next to the pipeline.
- **The natural-language-to-profile step is done by the MCP client's LLM, not by the server.** The tool's input schema *is* the profile format, so the model fills in structured arguments. The server only validates and saves. This means the server makes no LLM calls itself.
- **Transports:**
  - `stdio` for Claude Desktop or Claude Code on the Mac. This is the zero-UI dev path the team can use from day one.
  - Streamable HTTP for the Quest page (section 5).

**Tools (6):**
1. `list_targets()` returns the registry: targets, allow rules, and each target's quality note. Returning the quality notes lets the LLM tell the user honestly when a request is a long shot.
2. `save_profile(name, said, faces, allow, blur, tuning?)` validates the profile (pydantic or a plain check), writes `profiles/<name>.yaml`, and returns the normalized profile or clear errors. Updating an existing profile is the same call with the same name, so tuning is just saving again.
3. `get_profile(name)` and `list_profiles()`.
4. `test_profile(name)` runs the synthetic suite (section 4). It returns a short caught/missed/false-blur summary per target and the path to an HTML report with thumbnails.
5. `activate_profile(name)` points `profiles/active.yaml` at the profile. The running `app.py` notices the file change and reloads; checking the file's timestamp once a second is enough. All sources (webcam, Mentra, Ray-Ban) then use it.

**What tuning looks like:**
1. The user says "it missed the X-ray on the lightbox".
2. The LLM changes prompts or `min_score` and calls `save_profile`, then `test_profile`.
3. The user sees the new report, and then tries it live.

No extra tooling is needed.

**Not doing:** auth, multi-user, versioning beyond overwriting the file, remote deployment.

---

## 4. Synthetic-data testing

**Options:**
- **A. Paste-in composites.** Take still images of each target (with transparency or tightly cropped), paste them onto background frames, and move the virtual camera with the existing `eval synth` code (pan, zoom, whip, blur). Ground-truth boxes come free, because we placed the images.
- **B. Fully generated scenes.** A text-to-image model makes e.g. "doctor's desk with an X-ray on a lightbox". The scenes are more realistic but unlabeled, so the check is image-level only ("did anything get blurred where the X-ray is?") and needs auto or manual boxes.
- **C. Real recorded clips.** These already exist in the corpus plan. Not synthetic, but they are the reality check.

**Pick A.** It reuses `cmd_synth` and the coverage metric almost directly. It is deterministic, so a tuning change shows a real difference, and it runs in seconds thanks to caching. Use B for only a handful of showcase stills if time allows. Keep 1–2 real clips per demo target (C) so the team knows how big the gap between synthetic and real is.

**Assets:** `synthetic/assets/<target>/{pos,neg}/`, about 10–20 images each.
- **Positives (`pos`):** images that should be blurred.
- **Negatives (`neg`):** look-alikes that should *not* be blurred, so false blurs can be measured. Examples: adult faces for `child`, a blank page or a photo print for `xray`, other drink brands for `drink_brand`.
- **Sources:** public-domain or CC images (e.g. Wikimedia Commons X-rays, specimen ID/card templates). **Children's and adult faces should be generated images, not real photos**, which also gives a known "age".

**Report** (reuses the existing run/report flow), per target in the profile:
- caught N/M (blur covers ≥ X% of the box in ≥ Y% of frames, reusing the `VIS_OK` coverage logic)
- missed list with thumbnails
- false blurs on negatives
- per-stage timing

**Gates:** the existing leak gate becomes "0 missed positives for face-type rules". Object targets are reported as trends, not hard gates. Peace-sign consent is checked with the real recorded clips already in the eval plan; synthetic hands aren't worth building.

---

## 5. The Quest's role

The Quest is an authoring screen and nothing more. The logic stays on the Mac, as the vision says.

**Options, lightest first:**
1. **Quest browser → claude.ai with the MCP server added as a custom connector.** No code on the Quest. The catch: the server must be reachable over public HTTPS (a tunnel such as cloudflared or ngrok), and you depend on venue Wi-Fi and the connector setup. Verify connector requirements before relying on this.
2. **A single web page served by the Mac, opened in the Quest browser.**
   - The page has a text box (the Quest keyboard's dictation mic gives voice input; verify on the device), a chat log, the current profile as YAML, and the latest test report thumbnails.
   - Behind it is a small Mac-side chat loop: an LLM API call with the MCP server's tools attached, about 100 lines.
3. A native Quest app (Unity/Android). **Cut.**

**Pick option 2** for the demo. The team controls it, it shows the test report inside the headset, and it only needs the Quest and the Mac on the same network plus the LLM API. Use Claude Desktop over stdio on the Mac for development and as a fallback if the Quest misbehaves on stage. Option 1 is a fine stretch if it works on the first try.

---

## 6. Changes to the plan

**Add:**
- `privacy_filter/profile.py`: load and validate a profile, then compile it into the config overlay and target list. Include the targets in the perception cache key.
- `privacy_filter/targets.py`: the registry, replacing `sensitive.py` and the category toggles. YOLO-World runs only the prompts the active profile needs. Turn on the `genderage` module when a profile uses `child`.
- Face decision in the logic stage: `faces.default`, then `blur_if`, then `allow`, using the precedence in section 2. Object regions join the timeline as always-blurred tracks.
- `metrics.expected()` driven by the profile.
- `mcp_server.py` (section 3).
- `python -m eval profile-test <name>`: the synthetic suite in section 4, which the MCP `test_profile` tool calls.
- Profile hot-reload in `app.py`.
- `quest/`: the web page and chat loop (section 5).
- Three or four ready-made demo profiles from the vision's examples: consent, doctor, vlogger, streamer.

**Cut or defer** (none of these serve the vision's story):
- Blur style cycling (frosted/silhouette/emoji). Keep `soft`.
- Film-grain matching and the Face Landmarker outline mask.
- SAM 3 photo masks.
- Multi-Mac `--remote` sharding.
- The interactive labeler (`eval/label.py`). Synthetic data comes with labels.
- Detailed `degrade.py` Mentra profiles. Keep one, or none.
- Audio cues beyond a consent chime.
- OCR/Luhn: keep only if the `card` target is in a demo profile; otherwise defer.
- Favorites: keep as an optional `allow` entry only if it's already cheap; it isn't in the vision.
- Retroactive consent for recordings is already implemented in `process.py`. Keep it, but don't extend it.
- **Glasses input:** pick *one* (whichever streams first; Mentra's RTMP source is closer to existing code than the Ray-Ban iOS app). The profile story doesn't depend on which one.

**Build order** (ordered milestones, three parallel lanes):

| Milestone | A: pipeline | B: MCP + testing | C: Quest + assets + demo |
|---|---|---|---|
| 1. Profile parity | `profile.py`; the current face/consent behavior driven by a `consent` profile; profile-aware metrics | Profile schema and validation; `mcp_server.py` with save/get/list/activate over stdio | Collect or generate assets for document, plate, child, X-ray, drink brand (pos/neg) |
| 2. Objects | `targets.py` with YOLO-World for document and plate; regions in the timeline; hot-reload | `profile-test` on paste-in composites and the report; `test_profile` tool | Quest web page and chat loop against the HTTP transport |
| 3. Hard targets | `child` via genderage; X-ray and drink-brand paths (YOLO-World, then CLIP or OCR fallback) | Tune thresholds with the test suite; note real numbers in `list_targets` | Demo script: doctor authors on Quest → test → tune → activate → wear |
| 4. Wear it | One glasses source end to end with the active profile | Real-clip sanity checks per demo target | Rehearse the stdio fallback path |

Milestone 1 is the important one. Once a YAML profile drives today's behavior and Claude Desktop can save and activate it, the vision is real; everything after that adds targets and polish.

---

## 7. Risks

- **Hard targets underdeliver.** X-ray, children's faces and drink brands may be unreliable. Mitigation: the test report shows this honestly, the demo uses staged assets, and the pitch doesn't claim real-world accuracy for them.
- **Face-visible profiles fail open.** In the doctor profile adults are visible by default, so a missed age prediction leaks a child's face. Mitigation: a high age cutoff, blur when unsure (no age estimate, or a face too small to estimate), and the same buffer/backfill as today.
- **Too much compute.** YOLO-World on `mps` plus faces plus age, at 30 fps output, can hurt the smoothness priority. Mitigation: run objects at about 5 Hz with carried boxes, and run only the prompts the profile needs.
- **Synthetic-to-real gap.** Pasted images are easier than real scenes. Mitigation: 1–2 real clips per demo target.
- **The LLM writes odd profiles** (made-up targets, overly broad prompts). Mitigation: a registry-only schema, validation errors returned to the LLM, and the test step as the check.
- **Quest networking or venue Wi-Fi.** The Mac and Quest need the same network, and the LLM API needs internet. Mitigation: Claude Desktop on the Mac as the fallback author path.
- **Scope.** With three people, the profile and MCP work competes with the glasses input. Mitigation: milestone 1 comes before any glasses work, and the cut list above is real.
