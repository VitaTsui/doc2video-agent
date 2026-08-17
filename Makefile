.PHONY: help install install-renderer doctor demo run serve test lint fmt schemas docker clean

help:
	@echo "install           安装 Python 依赖（含内置 ffmpeg 与 dev）"
	@echo "install-renderer  安装 Remotion 渲染器依赖"
	@echo "doctor            检查运行环境与可用能力"
	@echo "demo              生成示例 pptx 到 tmp/demo.pptx"
	@echo "run               用示例文档跑一遍完整流程"
	@echo "serve             启动 API 服务"
	@echo "test / lint / fmt 测试、检查、格式化"
	@echo "schemas           导出 JSON Schema"
	@echo "docker            构建含 LibreOffice 的镜像"
	@echo "clean             清理运行期产物"

install:
	uv venv --python 3.12
	uv pip install -e ".[bundled,dev]"

install-renderer:
	cd renderer && pnpm install

doctor:
	uv run doc2video doctor

demo:
	uv run python scripts/make_demo.py tmp/demo.pptx

run: demo
	uv run doc2video run tmp/demo.pptx "生成一个3分钟的产品讲解视频，面向企业客户，第5页重点讲"

serve:
	uv run doc2video serve --reload

test:
	uv run pytest -q

lint:
	uv run ruff check .
	cd renderer && pnpm typecheck

fmt:
	uv run ruff check --fix .
	uv run ruff format .

schemas:
	uv run doc2video export-schemas

docker:
	docker build -t doc2video-agent .

clean:
	rm -rf storage tmp .pytest_cache .ruff_cache renderer/out renderer/public/staged
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
