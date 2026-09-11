## What changes

<!-- One or two sentences: what is different after this PR, and why. -->

## How it was verified

<!--
Commands you actually ran, with their output. A prediction is not a verification —
if you could not run something, write "not run, because …" instead of leaving it out.
-->

```
$ make test-ci
$ make lint
$ make demo
$ make check-web
```

- [ ] `make test-ci` — tests with the network block
- [ ] `make lint`
- [ ] `make demo` — end-to-end run
- [ ] `make check-web` — only if `web/` or an engine rule ported to it changed

## What is not covered

<!--
What you could not check and why; anything left deliberately unfixed. If you fixed a
defect, say how many other places had the same shape (`grep`) and whether they are fixed.
-->
