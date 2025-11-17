.PHONY: hello env lint setup run clean test check-ollama install

RED=\033[0;31m
GREEN=\033[0;32m
YELLOW=\033[0;33m
BLUE=\033[0;34m
MAGENTA=\033[0;35m
CYAN=\033[0;36m
RESET=\033[0m

hello:
	@echo "${MAGENTA}Hello, $$(whoami)!${RESET}"
	@echo "${GREEN}Current Time:${RESET}\t\t${YELLOW}$$(date)${RESET}"
	@echo "${GREEN}Working Directory:${RESET}\t${YELLOW}$$(pwd)${RESET}"
	@echo "${GREEN}Shell:${RESET}\t\t\t${YELLOW}$$(echo $$SHELL)${RESET}"
	@echo "${GREEN}Terminal:${RESET}\t\t${YELLOW}$$(echo $$TERM)${RESET}"

env:
	@echo "To activate the Poetry environment, run:"
	@echo "source $$(poetry env info --path)/bin/activate"

lint:
	@echo "${CYAN}Running linter...${RESET}"
	@source $$(poetry env info --path)/bin/activate && pre-commit run --all-files
	@echo "${GREEN}Done.${RESET}"

setup:
	@echo "${CYAN}Running setup checks...${RESET}"
	@python setup.py

install:
	@echo "${CYAN}Installing dependencies...${RESET}"
	@pip install --upgrade pip
	@pip install -r requirements.txt
	@python punkt_downloader.py
	@echo "${GREEN}Installation complete!${RESET}"

check-ollama:
	@echo "${CYAN}Checking Ollama status...${RESET}"
	@ollama list || echo "${RED}Ollama not found or not running${RESET}"

run:
	@echo "${CYAN}Starting Voice Assistant...${RESET}"
	@python main.py

clean:
	@echo "${CYAN}Cleaning up...${RESET}"
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete
	@find . -type f -name "*.pyo" -delete
	@find . -type f -name "*.log" -delete
	@echo "${GREEN}Cleanup complete!${RESET}"

test:
	@echo "${CYAN}Testing components...${RESET}"
	@python -c "import sounddevice; print('Audio devices:', len(sounddevice.query_devices()))"
	@python -c "import whisper; print('Whisper: OK')"
	@python -c "import torch; print('PyTorch: OK')"
	@echo "${GREEN}Basic tests passed!${RESET}"

help:
	@echo "${CYAN}Available commands:${RESET}"
	@echo "  ${GREEN}make hello${RESET}       - Show system info"
	@echo "  ${GREEN}make install${RESET}     - Install all dependencies"
	@echo "  ${GREEN}make setup${RESET}       - Run setup checker"
	@echo "  ${GREEN}make check-ollama${RESET} - Check Ollama status"
	@echo "  ${GREEN}make run${RESET}         - Start the voice assistant"
	@echo "  ${GREEN}make test${RESET}        - Test basic functionality"
	@echo "  ${GREEN}make lint${RESET}        - Run code linter"
	@echo "  ${GREEN}make clean${RESET}       - Clean up cache files"
	@echo "  ${GREEN}make env${RESET}         - Show virtual env activation"
	@echo "  ${GREEN}make help${RESET}        - Show this help message"

