import torch
import os
import gc
from transformers import BertTokenizer, BertForMaskedLM
from .abstract_module import AbstractModule
from typing import Optional, Tuple
import math

class BertModule(AbstractModule):
    LAYER_MIX_MODE_LIST = ["first", "final", "average"]
    HEAD_MIX_MODE_LIST = ["first", "average", "all"]

    def __init__(self, language="english"):
        super().__init__()
        # name = "english"
        # name = "chinese"
        self.language = language.lower()
    
    @staticmethod
    def bert_attention_forward(
        self,
        hidden_states: torch.Tensor,
        attention_mask: Optional[torch.FloatTensor] = None,
        head_mask: Optional[torch.FloatTensor] = None,
        encoder_hidden_states: Optional[torch.FloatTensor] = None,
        encoder_attention_mask: Optional[torch.FloatTensor] = None,
        past_key_value: Optional[Tuple[Tuple[torch.FloatTensor]]] = None,
        output_attentions: Optional[bool] = False,
    ) -> torch.Tensor:
        mixed_query_layer = self.query(hidden_states)

        key_layer = self.transpose_for_scores(self.key(hidden_states))
        query_layer = self.transpose_for_scores(mixed_query_layer)

        use_cache = past_key_value is not None
        # Take the dot product between "query" and "key" to get the raw attention scores.
        attention_scores = torch.matmul(query_layer, key_layer.transpose(-1, -2))

        attention_scores = attention_scores / math.sqrt(self.attention_head_size)
        if attention_mask is not None:
            attention_scores = attention_scores + attention_mask

        attention_probs = torch.nn.functional.softmax(attention_scores, dim=-1)
        attention_probs = attention_probs.squeeze(0)
        return attention_probs

    def load(self):
        path = os.path.dirname(os.path.abspath(__file__))
        self.model = BertForMaskedLM.from_pretrained(os.path.join(path, self.language))
        self.tokenizer = BertTokenizer.from_pretrained(os.path.join(path, self.language))
        def gen_hook(index):
            def hook(module, input, output):
                self.buffer[index, :, :, :] = self.bert_attention_forward(module, *input)
            return hook
        for ind, layer in enumerate(self.model.bert.encoder.layer):
            layer.attention.self.register_forward_hook(gen_hook(ind))
    
    def unload(self):
        del self.model
        del self.tokenizer
        gc.collect()

    def get_name(self):
        return f"BERT {self.language.capitalize()}"
    
    def get_description(self):
        return f"<h1><a href=''>{self.get_name()}</a></h1>" \
               f"<p>This is a pretrained module to do cloze test in {self.language.capitalize()}.</p>"
    
    def forward(self, sentence):
        inputs = self.tokenizer(sentence.replace("_", "[MASK]"), return_tensors="pt", padding=True, truncation=True, max_length=128)
        self.buffer = torch.zeros((12, 12, inputs["input_ids"].shape[1], inputs["input_ids"].shape[1]))
        self.input = self.tokenizer.convert_ids_to_tokens(inputs["input_ids"].squeeze(0))
        self.input_ind_map = {token: ind for ind, token in enumerate(self.input)}
        with torch.no_grad():
            outputs = self.model(**inputs)
        logits = outputs.logits
        mask_token_index = torch.where(inputs["input_ids"][0] == self.tokenizer.mask_token_id)[0]
        if len(mask_token_index) > 0:
            mask_logits = logits[0, mask_token_index, :]
            predicted_token_ids = torch.argmax(mask_logits, dim=-1)
            predicted_tokens = self.tokenizer.convert_ids_to_tokens(predicted_token_ids)
            for token in predicted_tokens:
                sentence = sentence.replace("_", token, 1)
            output = self.tokenizer(sentence, return_tensors="pt", padding=True, truncation=True, max_length=128)
            self.output = self.tokenizer.convert_ids_to_tokens(output["input_ids"].squeeze(0))
        else:
            self.output = self.input
    
    def get_input(self):
        try:
            return self.input
        except AttributeError:
            raise RuntimeError("Please run forward() first.")
    
    def get_output(self):
        try:
            return self.output
        except AttributeError:
            raise RuntimeError("Please run forward() first.")
    
    def get_attention_weights(self, key: str, query: str, layer_mix_mode: str, head_mix_mode: str):
        "k word need q word's attention"
        key = self.input_ind_map[key]
        query = self.input_ind_map[query]
        if head_mix_mode == "average":
            if layer_mix_mode == "first":
                return self.buffer.mean(dim=1)[0, key, query]
            elif layer_mix_mode == "final":
                return self.buffer.mean(dim=1)[-1, key, query]
            elif layer_mix_mode == "average":
                return self.buffer.mean(dim=1).mean(dim=0)[key, query]
            else:
                raise ValueError(f"Unsupported layer mix mode: {layer_mix_mode}")
        elif head_mix_mode == "first":
            head = 0
        elif head_mix_mode == "all":
            head = slice(None)
        else:
            raise ValueError(f"Unsupported head mix mode: {head_mix_mode}")
        if layer_mix_mode == "first":
            return self.buffer[0, head, key, query]
        elif layer_mix_mode == "final":
            return self.buffer[-1, head, key, query]
        elif layer_mix_mode == "average":
            return self.buffer.mean(dim=0)[head, key, query]
        else:
            raise ValueError(f"Unsupported layer mix mode: {layer_mix_mode}")
        
    
    def get_layer_mix_mode_list(self):
        return self.LAYER_MIX_MODE_LIST
    
    def get_head_mix_mode_list(self):
        return self.HEAD_MIX_MODE_LIST

