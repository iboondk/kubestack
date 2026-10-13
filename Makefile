.PHONY: tools cluster-up down image bootstrap seal lint validate test load chaos chaos-dry repo-url

tools:
	bash scripts/tools.sh

cluster-up:
	bash scripts/cluster-up.sh

down:
	bash scripts/cluster-down.sh

image:
	bash scripts/build-image.sh

bootstrap:
	bash scripts/bootstrap-argocd.sh

seal:
	bash scripts/seal-secrets.sh

lint:
	bash scripts/lint.sh

validate:
	python3 tools/validate.py

test:
	python3 -B -m unittest discover -s tests -v

load:
	bash scripts/load.sh

chaos:
	bash scripts/chaos.sh

chaos-dry:
	bash scripts/chaos.sh --dry-run

# Rewrites the git repoURL in the Argo apps for a fork: make repo-url REPO_URL=https://github.com/<you>/<repo>.git
repo-url:
	@test -n "$(REPO_URL)" || { echo "Set REPO_URL, e.g. make repo-url REPO_URL=https://github.com/<you>/kubestack.git" >&2; exit 2; }
	sed -i.bak -E 's|repoURL: https://github.com/[^[:space:]]*\.git|repoURL: $(REPO_URL)|' argocd/root-app.yaml argocd/apps/*.yaml
	rm -f argocd/root-app.yaml.bak argocd/apps/*.yaml.bak
