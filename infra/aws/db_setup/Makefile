# SAM build: copy handler and install dependencies so pymysql is in the deployment package.
build-DbSetupFunction:
	cp handler.py $(ARTIFACTS_DIR)/
	cp requirements.txt $(ARTIFACTS_DIR)/
	python -m pip install -r requirements.txt -t $(ARTIFACTS_DIR) --quiet
	rm -rf $(ARTIFACTS_DIR)/bin
