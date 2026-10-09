# Driver rollout dependency from the 2026-10-09 audit

The Steam Controller branch currently bundles `v0.1.0-beta.108`, source
`68724c4e6cae11e1508b6f835433ab55e5c3212d`. The package contract,
`third-party/libvirtualgamepad` gitlink and Windows workflow all agree on that
revision. They do not contain the later producer fixes:

- `a7b16b9`: wait for in-flight VHF submits before teardown; haptic queue;
  per-send keep-alive arming.
- `26a9ba5`: forward declaration required by the preceding change.
- The audit branch's fixes for per-controller keep-alive deadlines, protected
  keep-alive submissions and worker restart state.

Updating source headers or the submodule alone cannot update the independently
built driver binary. After the producer change passes a Windows WDK build and
controller tests, create a new producer package and update the release tag,
archive SHA-256, source revision, DriverVer and matching workflow validation
values together. Validate its manifest and signature through the existing
consumer signing path. The audit intentionally does not invent an archive
checksum or publish a release.

The touch-ownership fix in this PR is independent of that driver release and
retains the existing package contract. Before shipping the combined controller
fixes, test hot-unplug under input/haptic load and two controllers where one is
busy and the other is idle. Check actual installed driver provenance, not only
the checked-out source revision.
