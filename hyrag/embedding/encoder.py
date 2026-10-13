from __future__ import annotations

import logging
import os
from functools import lru_cache #for caching result returned from function calls
import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoTokenizer #from HuggingFace library we installed

MODEL_NAME = "BAAI/bge-m3"
DIM = 1024 #word embedding dimension, default of BAAI/bge-m3
MAX_TOKENS = 8192 #also default of BAAI/bge-m3, the only thing we decide is chunk size