#!/bin/zsh
# Local hourly runner - stopgap while GitHub's scheduler stays inactive on this
# new repo. Bonus: a residential IP is not blocked by cars.com the way Actions
# runners are, so this run usually gets that source too.
#
# Remove with:  launchctl bootout gui/$(id -u)/com.scotty.carbot
set -u
cd "$(dirname "$0")" || exit 1
export PATH="/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"
export PYTHONPATH=src

echo "=== $(date -u '+%Y-%m-%dT%H:%M:%SZ') local run ==="
git pull --rebase --autostash -q origin main 2>&1 | tail -2
./.venv/bin/python -m carbot.main
rc=$?
if ! git diff --quiet -- state/listings.json; then
  git add state/listings.json
  git -c user.name="carbot-local" -c user.email="carbot@users.noreply.github.com" \
      commit -q -m "chore(state): update listings (local run) [skip ci]"
  git push -q origin main && echo "state pushed"
fi
echo "=== exit $rc ==="
exit $rc
