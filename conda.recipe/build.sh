#!/bin/bash
# install using pip from the wheel provided by PyPI

if [ $(uname) == Darwin ]; then
  if [ "$PY_VER" == "3.7" ]; then
    pip install https://pypi.io/packages/source/b/bcolz-zipline/bcolz_zipline-1.2.6-cp37-cp37m-macosx_10_15_x86_64.whl
  else
    if [ "$PY_VER" == "3.8" ]; then
      pip install https://pypi.io/packages/source/b/bcolz-zipline/bcolz_zipline-1.2.6-cp38-cp38m-macosx_10_15_x86_64.whl
    else
      if [ "$PY_VER" == "3.9" ]; then
        pip install https://pypi.io/packages/source/b/bcolz-zipline/bcolz_zipline-1.2.6-cp39-cp39-macosx_10_15_x86_64.whl
      else
        if [ "$PY_VER" == "3.10" ]; then
          pip install https://pypi.io/packages/source/b/bcolz-zipline/bcolz_zipline-1.2.6-cp310-cp310m-macosx_10_15_x86_64.whl
        else
          if [ "$PY_VER" == "3.11" ]; then
            pip install https://pypi.io/packages/source/b/bcolz-zipline/bcolz_zipline-1.2.6-cp311-cp310m-macosx_10_15_x86_64.whl
          else
            echo "Python version not supported"
            exit 1
          fi
        fi
      fi
    fi
  fi
fi
