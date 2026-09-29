'use strict';
// Zylos post-upgrade hook: post-install's steps in light mode. Zylos runs it
// synchronously while holding the component lock, so it only makes sure uv,
// Python and the harness CLI's env still work and reconciles the scheduler
// tasks to this release's manifest (updated prompts, a dropped task
// removed); the endpoint, doctor and scope checks are left to
// `node zylos/hooks/post-install.js`. The data dir is never touched.

require('./post-install.js').main({ upgrade: true });
