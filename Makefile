.PHONY: run test docker

run:
	uv run uvicorn freshflow.app:app --port 8077

test:
	uv run pytest tests -q

# Same image the devcontainer uses. Drops you into a shell with the repo mounted.
docker:
	touch .env
	docker build -t freshflow .
	docker run --rm -it --env-file .env -p 8077:8077 -v "$(CURDIR)":/work freshflow bash
