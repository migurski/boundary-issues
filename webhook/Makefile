index.zip: webhook.py task.py finish.py sweep.py
	pip install --quiet --target /tmp/lambda-deps PyYAML
	zip -j index.zip webhook.py task.py finish.py sweep.py
	cd /tmp/lambda-deps && zip -r $(CURDIR)/index.zip yaml --exclude "yaml/*.so" --exclude "yaml/__pycache__/*"
