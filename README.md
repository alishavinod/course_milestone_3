## Milestone 3

In the previous milestones, you wrote specs and checks for components A to E and had the agent
build the whole app from scratch.

<mark>Milestone 3 is <b>open ended</b>. You build <b>at least 2</b> improvements to how the
agent works on the app, explain the problem each one solves, run them, and measure whether they
help. You may build more than 2.</mark>

Some ideas:

- **A specialized tool:** a script the agent can call, for example one that reports which
  endpoints the app does not serve yet, or one that summarizes a long log so the agent sees
  only the errors.
  - *What to change:* put it in `milestone-3-specs/` and tell the agent about it in
    `/spec/README.md`.
- **Memory:** a way for the agent to keep track of what it has done and what is left.
  - *What to change:* tell the agent in `/spec/README.md` to keep a notes file, or have the
    agent code ([`mini-swe-agent/`](mini-swe-agent/src/minisweagent/agents/default.py)) add
    the notes to every request.
- **Improved specs:** a way of writing specs and checks you have not tried before, for example
  checks that point to the likely faulty file, or a time plan for the 50 minutes.
  - *What to change:* the files in `milestone-3-specs/`.
- **Your own idea:** you are encouraged to come up with new and creative improvements. There
  are many ways to build the ideas above, and endless others to try.

> [!TIP]
> **What you can change:**
>
> - **`milestone-3-specs/`:** specs, checks, tools, memory files, or anything else the agent
>   uses during a run. It is copied into the agent's container as `/spec`, so a tool placed
>   here needs no Docker changes. A tool may install packages it needs when it runs.
> - **The agent's prompts:** in
>   [`agent_llm_config/mswea-openai-gpt-5.6-luna-no-reasoning.yaml`](tasks/slack-clone-course/course-staff-files/final_task/agent_llm_config/mswea-openai-gpt-5.6-luna-no-reasoning.yaml)
>   (but not the model or its settings).
> - **The agent's code:** anything in [`mini-swe-agent/`](mini-swe-agent/).
>
> Everything else stays as it is.

> [!WARNING]
> **What you cannot do:**
>
> - **Do not count an idea from a past milestone as one of your improvements.** You may include
>   past work in your submission (for example, your Run 5.3 specs and checks from milestone 2),
>   but things you already did or tested in milestones 0 to 2, such as the staged layout, the
>   `score_only` vs `verbose` testing modes or the regression suite, do not count as new
>   improvements. Each improvement must be something different.
> - **Do not modify `instruction.md`.**
> - **Do not change the model or the budget.** The agent stays GPT 5.6 Luna, and no run should
>   cost more than about $3.
> - **Do not call another model or outside service.** For example, a tool that asks a
>   different LLM for help is not allowed.
> - **Do not give the agent the app.** Your tools, memory and specs may not contain code for
>   the app itself or templates that generate it. The agent must still write `/app`.
> - **Do not change how runs are set up:** the run scripts (e.g. `run_student_openai.sh`,
>   `run_end_to_end.sh`), `task.toml` and the Docker environment stay as they are.

<mark>To understand the guidelines for milestone 3, use this <b>README</b>.</mark>

Below, we outline the core tasks for Milestone 3:

- [The report](#the-report-reportmd) (`report.md`), in four sections:
  - [Implementation](#implementation): the improvements you build, and why
  - [Experiments](#experiments): the runs you do to test them
  - [Analysis](#analysis): what your results show
  - [Replay instructions](#replay-instructions): how staff replay your final setup
- [Class presentation](#class-presentation): a 10 minute talk in class on December 4

> [!IMPORTANT]
> <i>**You will probably still need checks.** Even if your improvement is not a spec (for
> example, a tool or memory), you need a way to measure whether it helps. You may use your full
> component A to E checks from milestone 2 for this.</i>
>
> <i>Recall that the agent could not break the four milestone 0 boot conditions (see
> [`milestone-0-specs/README.md`](tasks/slack-clone-course/course-staff-files/final_task/environment/milestone-specs/milestone-0-specs/README.md)).
> The same should hold here.</i>

## The report (`report.md`)

Fill in the template in [`report.md`](report.md). It has four sections:

### Implementation

Build at least 2 improvements. For each one, give:

- **What it is:** what you built and where it lives (e.g. `milestone-3-specs/`, the agent's
  prompts, or `mini-swe-agent/`)
- **Why:** the problem it solves, and why it is new compared with past milestones

### Experiments

Run each setting **at least 2 times**. Between settings, change only the improvements.

| Setting | What the agent gets |
| --- | --- |
| **Baseline** | an empty `/spec` (leave `milestone-3-specs/` empty), so the agent works from `instruction.md` alone |
| **Improvement 1 only** | baseline + improvement 1 |
| **Improvement 2 only** | baseline + improvement 2 |
| **All improvements** | baseline + every improvement (your final setup) |

If you build more than 2 improvements, add an "only" setting for each one.

> [!NOTE]
> You may also add a setting that starts from your milestone 2 setup (your Run 5.3 specs and
> checks), for example milestone 2 setup + every improvement.

### Analysis

For each improvement, answer:

1. **Does it help?** Compare it with the baseline on your checks.
2. **Did the agent use it?** Show transcript evidence (e.g. how often it called your tool).
3. **Why or why not?** Explain with transcript evidence. Negative results are fine if explained.

Then: **do the improvements work well together?** Compare "All improvements" with each alone.

For each question, name the runs you compared, give a results table, and note how consistent
the repeats are.

### Replay instructions

Keep every change inside the repo, and list every file you changed or added, any setup steps,
and the exact commands to replay your final setup ("All improvements") from an empty `/app`.
Staff follow these as written to run the hidden tests.

## Class presentation

On **December 4**, you present your work in class in **10 minutes**. There is no required
format: slides, a Claude artifact, a live demo or anything else that helps you explain your
work is fine. Focus on your implementation choices, experiments and analysis:

- **Implementation:** the problem each improvement solves, and how you built it
- **Experiments:** the runs you did and the results
- **Analysis:** what worked, what did not, and why

> [!TIP]
> Keep in mind the following things as you are developing:
>
> - **Debug then "freeze" your setup:** <i>before your runs, you may want to do a few practice
>   runs to fix bugs in your tools, memory, specs or checks. Once you are confident, stop editing
>   anything except the improvement you are testing, so that any change in results comes from
>   that improvement.</i>
> - **Compare against a baseline:** <i>to show that your change helps, run the same setup with
>   and without it, and keep everything else the same.</i>
> - **Recall the role of `/spec/README.md`:** <i>it is the agent's entry point.</i>
>   - <i>It is where you tell the agent about anything you add, such as a tool it can call or
>     a memory file it should read and update. A tool the agent is never told about may never
>     be used.</i>
>   - <i>Make sure you are also instructing the agent to not edit the files in `/spec`.</i>
> - **Time limit:** <i>recall that mini-swe-agent cuts off any single shell command after 30 seconds by
>   default, so your checks must finish in under 30 seconds.</i>
> - **Be careful not to kill the agent itself:** <i>killing a process by name (`pkill -f`,
>   `killall`, `pgrep -f`, or parsing `ps`) can match the agent too. Kill processes only by a
>   saved PID or by the PID that owns a port (found with `ss -ltnp`).</i>
> - **Getting results for your analysis:** <i>you may write any other scripts you need, for
>   example one that reruns your checks on the agent's final code in `artifacts/app/` after a
>   run. Keep them outside `milestone-3-specs/`, so the agent does not see them.</i>
> - **If you are improving the specs and are stuck on how to write one:** <i>consider reviewing the examples in
>   Section 3 of [milestone 2's `GUIDE.md`](https://github.com/comsw4995-f26/course_milestone_2/blob/main/GUIDE.md#3-example-specs).</i>

## 1. Set up (once)

> [!NOTE]
> You should already have done steps 1 to 3 (Docker, `uv` and your key) in milestone 0. They are
> here just in case. **Step 4 changes for milestone 3:** the spec link now points at
> `milestone-3-specs`.

1. **Docker**, usable without `sudo`. Install it from the
   [official guide](https://docs.docker.com/get-docker/) (Docker Desktop on macOS and Windows; it
   must be running whenever you run the agent). On Linux, also run
   `sudo usermod -aG docker $USER` and log back in. Check it with:

   ```bash
   docker run hello-world
   ```

2. **`uv`**, the Python package manager the scripts use:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   uv --version
   ```

3. **Your OpenAI key.** Put the OpenAI key the TAs gave you in `dev.env` at the repo root.
   From the repo root (on Linux, drop the `''` after `-i`):

   ```bash
   sed -i '' 's|^OPENAI_API_KEY=.*|OPENAI_API_KEY="<your-openai-key>"|' dev.env
   ```

   You will run the agent with `run_student_openai.sh`. <mark>Please continue using your OpenAI
   credits until you reach the limit. When your OpenAI credits run out, please inform the TAs right
   away, and they will give you instructions on how to keep running.</mark>

> [!WARNING]
> **Monitor your usage. No run should cost more than about $3.** The cost is printed at the end
> of every run (`COST: total=$...`). If you run out of OpenAI credits, inform the staff
> immediately.
>
> **Your credits are limited, so you cannot rerun as many times as you like.** Make each run
> count: before running again, fix every problem you found in the last run.

4. **Link the spec folder.** The run script refuses to start without this link:

   ```bash
   cd tasks/slack-clone-course/student-files
   ln -sfn ../course-staff-files/final_task/environment/milestone-specs/milestone-3-specs spec
   ```

   `spec` now points at `milestone-3-specs/`, the folder that holds what the agent gets (your
   `/spec/README.md`, plus any specs, checks, tools or other files you give it). The run copies
   it into the agent's container as `/spec`, and the agent is told to read `/spec/README.md`
   first.

> [!TIP]
> **If you see `No module named 'mini_swe_agent_local'`,** your Python environment is out of
> date. From the repo root, run `uv sync --reinstall-package long-horizon`, then run the agent
> again. If that does not fix it, run `rm -rf .venv && uv sync`.

## 2. Running the agent

For every run:

1. Build your improvements, and the specs and checks if you use them.
2. Put everything the agent should get for that run directly in `milestone-3-specs/`
   (`tasks/slack-clone-course/course-staff-files/final_task/environment/milestone-specs/milestone-3-specs/`),
   with `README.md` at its top level. The run copies this folder into the agent's container as
   `/spec`, so that file becomes `/spec/README.md`.
3. Run the agent from `tasks/slack-clone-course/student-files`:

   ```bash
   ./run_student_openai.sh 3
   ```

4. Read the result (see [the section below](#3-reading-a-run) for how to read it). If you gave the agent checks,
   look in the transcript for whether it ran them, what they printed last, and whether it
   submitted with failures still showing.
5. Fix your setup, not the agent's code, before running again. Stay within your budget.

## 3. Reading a run

A run takes up to 50 minutes and ends with a table and a cost line:

```text
┏━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━┓
┃ Trials ┃ Exceptions ┃  Mean ┃
┡━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━┩
│      0 │          0 │ 0.000 │
└────────┴────────────┴───────┘
Results written to .../tasks/slack-clone-course/jobs/<timestamp>/result.json
COST: total=$<amount>
```

- **Stuck on `starting environment...`:** normal. The first Docker build can take up to 20
  minutes; later builds take seconds.
- **`Trials 0`, `Mean 0.000`:** expected. Ignore it.
- **`Exceptions 0`:** the agent ran and exited cleanly.
- **`COST: total=$...`:** what the run cost.
- **The run took a minute or two and cost very little:** the agent quit early. Run it again
  once; if it keeps happening, your spec or setup has a bug.

Results are in `tasks/slack-clone-course/jobs/<timestamp>/<trial>/`:

- **`agent/mini-swe-agent.txt`:** the full transcript. Check that the agent read your `/spec/README.md`,
  ran your checks, and what they printed last. Search it for your check scripts' file names to
  find each time the agent ran one, and for `PASS` or `FAIL` to see the results (see the tip in
  [section 5 of milestone 2's `GUIDE.md`](https://github.com/comsw4995-f26/course_milestone_2/blob/main/GUIDE.md#5-checks)).
- **`artifacts/app/`:** the code the agent wrote.
- **`result.json`:** timings, tokens and cost; `exception_info: null` means no crash.
- **`agent/format_errors.jsonl`:** malformed agent replies. A few are fine.

When a run goes wrong, look for a rule the agent misread, a rule your checks do not cover, or a
submit with failures still showing. Then change your setup.

## 4. What to submit

> [!IMPORTANT]
> **Submit the whole repo.** Make sure it contains:
>
> 1. **Your final `milestone-3-specs/` folder:** a `README.md` (the agent's `/spec/README.md`)
>    and every file it names (specs, checks, tools, or anything else you give the agent).
> 2. **The code for each improvement:** for example a tool or memory setup that lives outside
>    the spec folder (how to use it goes in the replay instructions in `report.md`).
> 3. **The `/spec` folder of each run:** for example in `spec-copies/<run>/`, so staff can see
>    exactly what the agent got in each run.
> 4. **The job folder of every run:** each `tasks/slack-clone-course/jobs/<timestamp>/` folder.
>    Each one holds the code the agent wrote (`artifacts/app/`, the run's `/app`), the
>    transcript, and `result.json`.
> 5. **`report.md`**, covering Implementation, Experiments, Analysis and Replay
>    instructions (see
>    [`report.md`](report.md) for a blank template), with a table of every run: the setting,
>    repeat #, job folder, check results, cost, how often the agent ran the checks, whether it
>    submitted with failures, and any problems.

### Before you submit: checklist

- [ ] You have read the important notes and tips above thoroughly.
- [ ] You built at least 2 improvements, none of them an idea from a past milestone.
- [ ] You only changed `milestone-3-specs/`, the agent's prompts and `mini-swe-agent/` (not
  `instruction.md`, the model, the run scripts, `task.toml` or the Docker environment).
- [ ] You ran the baseline, each improvement alone, and all improvements together, with 2
  repeats at minimum.
- [ ] You kept a copy of the `/spec` folder for each run.
- [ ] You kept the job folder of every run.
- [ ] You are ready to present your work in class on December 4 (10 minutes).
- [ ] Your `report.md` has replay instructions that work from a fresh copy of your repo.
- [ ] Your `report.md` covers Implementation, Experiments and Analysis, with:
  - [ ] the runs you compared and why
  - [ ] a table of the supporting results
  - [ ] evidence from the transcripts that the agent used each improvement
  - [ ] how consistent your results are across repeats

## 5. Grading

Grading happens in this order:

1. **Required files (<mark>Pass</mark> or <mark>Fail</mark>):** your repo contains everything listed in
   the [What to submit](#4-what-to-submit) section above.
2. **`report.md` (60%):** your write-up covers all four parts:
   - **Implementation:** each improvement works, is new, follows the rules above, and the
     transcripts show the agent actually used it.
   - **Experiments:** you ran every required setting and kept everything else the same.
   - **Analysis:** you answered each Analysis question with supporting results.
   - **Replay instructions:** staff can replay your final setup by following them as
     written. If staff cannot replay it, the hidden tests cannot be scored either.

3. **Presentation (10%):** your 10 minute class presentation on December 4 (see
   [Class presentation](#class-presentation)).
4. **Hidden tests (30%):** staff replay your final setup ("All improvements"), starting from an
   empty `/app`, and run the hidden tests on the **whole app**, covering the four milestone 0
   boot conditions and components A, B, C, D, and E.

> [!IMPORTANT]
> **Missing files mean a 0.** If any required file is missing, that part scores 0, and staff
> cannot replay your setup, so the hidden tests cannot be scored either.

---

<h1 align="center">SWE-Marathon</h1>

<p align="center"><em>Can agents autonomously complete ultra-long-horizon software work?</em></p>


## Links

- [Website](https://www.swe-marathon.org/)
- [Paper](https://arxiv.org/abs/2606.07682)
- [Leaderboard](https://www.swe-marathon.org/#leaderboard)
- [Tasks](https://www.swe-marathon.org/#tasks)


## News
- [09/2026] 🚀 Featured on the [Qwen3.8-Max model card](https://x.com/Alibaba_Qwen/status/2094968708288680276?s=20)!
- [08/2026] 🔶 Featured on the [Hy4 preview model card](https://hy.tencent.ai/research/hy4-preview)!
- [08/2026] ⭐ Featured on the [GLM 5.3 model card](https://z.ai/blog/glm-5.3)!
- [08/2026] ⚙️ Featured on the [Grok 4.6 model card](https://media.x.ai/v1/website/card-4p6-4cd2dc57.pdf)!
- [07/2026] ➕ SWE-Marathon [v1.1 released](https://x.com/rishi_desai2/status/2087216551653146706?s=20)!
- [07/2026] 🌙 Featured on the [Kimi K3 model card](https://www.kimi.com/blog/kimi-k3)!
- [07/2026] 🔥 Featured on the [Grok 4.5 model card](https://x.ai/news/grok-4-5)!
- [06/2026] 🚀 Featured on the [GLM 5.2 model card](https://z.ai/blog/glm-5.2)!
- [06/2026] ⚡ SWE-Marathon [v1.0 released](https://x.com/rishi_desai2/status/2062930906818769356?s=20)!


## Citation

```bibtex
@misc{swemarathon_2026,
  title        = {{SWE-Marathon: Can Agents Autonomously Complete Ultra-Long-Horizon Software Work?}},
  author       = {Rishi Desai and Jesse Hu and Joan Cabezas and Neel Harsola and Pratyush Shukla and Daniel Wang and Xiangyi Li and Roey Ben Chaim and Adnan El Assadi and Omkaar Mukund Kamath and Fenil Faldu and Prannay Hebbar and Jiankai Sun and Yiyuan Li and Pramod Srinivasan and Ishan Gupta and Christopher Settles and Derek Chen and Pranav Raja and Albert Liu and Marek Šuppa and Nevasini Sasikumar and Luyang Kong and Erik Quintanilla and Ivan Bercovich and Steven Dillmann},
  year         = {2026},
  howpublished = {\url{https://arxiv.org/abs/2606.07682}},
  note         = {arXiv:2606.07682}
}
```

## License

[Apache License 2.0](LICENSE)
