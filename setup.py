import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="rpi-bad-power",
    version="0.0.2",
    author="Xiaonan Shen",
    author_email="s@sxn.dev",
    description="A Python library to detect bad power supply on Raspberry Pi ",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/shenxn/rpi-bad-power",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX :: Linux",
    ],
    python_requires='>=3.6',
)
