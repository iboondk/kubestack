# Demo recording script

This script is for recording a short GIF or a Loom video of Kubestack. Two versions are given:

- **Offline cut (about 90 seconds).** Needs only Python and Go. Nothing runs on a cluster. Record this if Docker is not available.
- **Full cut (about 4 to 5 minutes).** Needs Docker, kind, kubectl, kubeseal and the Tenantly API source. Use it for a full walkthrough.

Record real output only. Do not edit numbers into the terminal, do not paste timings from another run, and cut any step that fails instead of hiding it. The chaos and load numbers in the full cut come from your own machine.

## Before you hit record

1. Open a terminal at the repo root, with a large font (16 pt or more) and a dark theme. Keep the prompt short, for example `export PS1='$ '`.
2. Clear the screen with `clear`.
3. Check the tools you plan to show:

   ```bash
   make tools && .tools/bin/helm version --short && .tools/bin/kubeconform -v
   ```

4. For the full cut only, check the Docker daemon and the cluster state:

   ```bash
   docker info --format '{{.ServerVersion}}'
   kind version
   ```

5. Set the recorder to capture the terminal window only. Set the frame rate to 15 fps for a GIF, or record at 1080p for a Loom.

## Offline cut (about 90 seconds)

| Time | Action | Command | What the viewer should see |
|---|---|---|---|
| 0:00 to 0:10 | Title card. Say what the project is in one sentence. | none | The README title on screen, or a text card |
| 0:10 to 0:25 | Show the layout | `ls` then `cat versions.env` | Pinned versions for kind, Helm, Argo CD and the charts |
| 0:25 to 0:40 | Run the unit tests | `make test` | Lines ending in `ok` and `Ran 78 tests` followed by `OK` |
| 0:40 to 0:60 | Run the offline validator | `make validate` | A list of PASS lines and a final summary line |
| 0:60 to 1:15 | Show the proof files | `head -20 proof/helm-lint.txt` | The helm lint output from the last real run |
| 1:15 to 1:30 | Dry-run the chaos plan | `make chaos-dry` | Lines prefixed `DRY-RUN:`, with no cluster |
| 1:30 | End card: link to the README | none | The repo URL |

If `make test` is slow on the first run, cut the recording and start again. Do not speed up the output with video editing.

## Full cut (about 4 to 5 minutes)

Run each step once before you record it, so the images are already pulled and the cluster is warm. Then record the whole sequence in one take, if you can.

| Time | Action | Command | What the viewer should see |
|---|---|---|---|
| 0:00 | Title card | none | Project name and one sentence |
| 0:15 | Offline checks | `make test` then `make lint` | The unit tests pass. `make lint` prints a summary block with one PASS or SKIPPED row per step |
| 0:45 | Create the cluster | `make cluster-up` | Three nodes reported Ready |
| 1:10 | Build and load the app image | `make image` | `kind load docker-image tenantly-api:dev` completes |
| 1:30 | Bootstrap Argo CD | `make bootstrap` | Argo CD installed and the root app `kubestack-root` applied |
| 1:50 | Watch the apps sync | `kubectl get applications -n argocd -w` | Six child apps moving to Healthy, in wave order |
| 2:30 | Show the HPA before load | `kubectl get hpa -n tenantly` | Current and target values for CPU and `tenantly_requests_per_second` |
| 2:45 | Generate load | `make load` | hey output in the terminal |
| 3:10 | Watch the replicas change | `kubectl get hpa -n tenantly -w` in a second pane | The replica count rising during load |
| 3:30 | Show Grafana | Open `http://localhost:30300` and log in as `admin` | The Tenantly autoscaling dashboard |
| 3:50 | Chaos run | `scripts/chaos.sh --experiment pod-kill --rounds 1` | Each round's recovery time, measured |
| 4:20 | Show the chaos report | `ls proof/chaos/` then `cat proof/chaos/chaos-report-*.md \| head -40` | The table of experiment, round and recovery_seconds |
| 4:45 | Tear down | `make down` | The cluster is deleted |

Notes for the full cut:

- The load step uses the default of 120 seconds. Shorten it with `scripts/load.sh --duration 60s` if you need to fit the time.
- `make load` only shows a change in replicas after the HPA's sync period. If the replicas do not change on camera, keep recording and say so in the video. Do not cut to an earlier run.
- If the chaos report is empty, the run did not finish. Show the failure and say what it was. Do not write a report by hand.

## Editing

- For a GIF, trim the start and end. Use `gifski` or `ffmpeg` at 15 fps, width 960 px. Keep the GIF under 10 MB so it loads in a README.
- For a Loom, keep the recording as it is. Add a title card only at the start.
- Do not add captions that state a number which is not on screen.

## Checklist after recording

- [ ] Every number shown on screen comes from that recording
- [ ] No `.env` contents, passwords or tokens are visible (run `make seal` off camera, and `clear` before showing any file under `.env`)
- [ ] No absolute file paths from your machine appear in the frame
- [ ] The GIF or Loom link is added to the README by hand, under the Quick start section
