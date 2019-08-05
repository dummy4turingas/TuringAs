# VoltaAs: SASS assembler for NVIDIA Volta and Turing

## Requirements:
* Pyhthon >= 3.6

## Usage:
To generate cubin file:
```
python main.py -i input.sass -o output.cubin -arch 70
```

## Supported hardware:
All NVIDIA Volta (SM70) and Turing (SM75) GPUs.

## Other features:
* Include files.
* Inline python code.