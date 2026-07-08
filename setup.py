"""TokenCoin setup configuration."""
from setuptools import setup, find_packages

with open("README.md", "r") as fh:
    long_description = fh.read()

setup(
    name="tokencoin",
    version="0.1.0",
    author="Sammy Lord",
    description="TokenCoin (TKC) - Privacy-First AI Cryptocurrency",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/sammylord/tokencoin",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Topic :: System :: Distributed Computing",
        "Topic :: Security :: Cryptography",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.10",
    install_requires=[
        "aiohttp>=3.9.0",  # HTTP client for Ollama API
    ],
    extras_require={
        "dev": [
            "pytest>=7.0",
            "pytest-asyncio>=0.21",
            "flake8>=6.0",
            "mypy>=1.0",
        ],
        "gpu": [
            "nvidia-ml-py3>=7.0",  # NVIDIA GPU monitoring
        ],
        "ollama": [
            "ollama>=0.1.0",  # Optional Python Ollama client
        ],
    },
    entry_points={
        "console_scripts": [
            "tokencoin=tokencoin.cli:main",
        ],
    },
)
