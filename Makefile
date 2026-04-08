.PHONY: install web cli docker-build docker-up demo

install:
	python3 -m venv .venv
	. .venv/bin/activate && pip install --upgrade pip && pip install -e .

web:
	. .venv/bin/activate && gis-agent-web

cli:
	. .venv/bin/activate && gis-agent --help

docker-build:
	docker build -t tjsoes-gis-agent .

docker-up:
	docker compose up --build

demo:
	. .venv/bin/activate && gis-agent --task "用示例行政区数据按 value 字段生成分级设色专题图，标题为 Sample Choropleth" --data examples/data/sample_polygons.geojson --output-dir output/demo --mode template --run
