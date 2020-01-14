import torch
from copy import copy
from transformers import BertTokenizer
from utils import CAPACITY, BLOCK_SIZE
import random
class Block:
    def __init__(self, tokens, ids, pos, blk_type=1, **kwargs):
        self.tokens = tokens
        self.ids = ids
        self.pos = pos
        self.blk_type = blk_type # 0 sentence A, 1 sentence B
        for k, v in kwargs:
            setattr(self, k, v)
    def __lt__(self, rhs):
        return self.blk_type < rhs.blk_type or (self.blk_type == rhs.blk_type and self.pos < rhs.pos)
    def __ne__(self, rhs):
        return self.pos != rhs.pos or self.blk_type != rhs.blk_type
    def __len__(self):
        return len(self.ids)

class Buffer:
    @staticmethod
    def split_document_into_blocks(d, tokenizer, cnt=0, hard=True, properties=None):
        '''
            d: [['word', '##piece'], ...] # a document of tokenized sentences 
            properties: [
                            [
                                (name: str, value: any), # len(2) tuple, sentence level property
                                (name: str, position: int, value: any) # len(3) tuple, token level property
                            ],
                            []... # len(d) lists
                        ]
        '''
        ret = Buffer()
        updiv = lambda a,b: (a - 1) // b + 1
        if hard:
            for sid, tsen in enumerate(d):
                psen = properties[sid]
                num = updiv(len(tsen), BLOCK_SIZE)
                bsize = updiv(len(num), num)
                for i in range(num):
                    st, en = i * bsize, min((i + 1) * bsize, len(tsen))
                    cnt += 1
                    tmp = tsen[st: en] + [tokenizer.sep_token]
                    # inject properties into blks
                    tmp_kwargs = {}
                    for p in psen:
                        if len(p) == 2:
                            tmp_kwargs[p[0]] = p[1]
                        elif len(p) == 3 and st <= p[1] < en:
                            tmp_kwargs[p[0]] = (p[1], p[2])
                        else:
                            raise ValueError('Invalid property {}'.format(p))
                    
                    ret.insert(Block(tmp, tokenizer.convert_tokens_to_ids(tmp), cnt, **tmp_kwargs))
        else:
            raise NotImplementedError
        return ret, cnt

    def __init__(self):
        self.blocks = []

    def __add__(self, buf):
        ret = Buffer()
        ret.blocks = self.blocks + buf.blocks
        return ret

    def __len__(self):
        return len(self.blocks)
    
    def __getitem__(self, key):
        return self.blocks[key]
        
    def clear(self):
        self.blocks = []

    def calc_size(self):
        return sum([len(b) for b in self.blocks])

    def block_ends(self):
        t, ret = 0, []
        for b in self.blocks:
            t += len(b)
            ret.append(t)
        return ret

    def insert(self, b, reverse=True):
        if not reverse:
            for index in range(len(self.blocks) + 1):
                if index >= len(self.blocks) or b < self.blocks[index]:
                    self.blocks.insert(index, b)
                    break
        else:
            for index in range(len(self.blocks), 0, -1):
                if index == 0 or b < self.blocks[index - 1]:
                    self.blocks.insert(index, b)
                    break

    def delete(self):
        pass

    def merge(self, buf):
        ret = Buffer()
        t1, t2 = 0, 0
        while t1 < len(self.blocks) or t2 < len(buf):
            if t1 < len(self.blocks) and (t2 >= len(buf) or self.blocks[t1] < buf.blocks[t2]):
                ret.blocks.append(self.blocks[t1])
                t1 += 1
            else:
                ret.blocks.append(self.blocks[t2])
                t2 += 1
        return ret
    
    def filtered(self, fltr: 'function blk, index->bool'):
        ret = Buffer()
        for i, blk in enumerate(self.blocks):
            if fltr(blk, i):
                ret.blocks.append(blk)
        return ret
            
    def random_sample(self, size):
        assert size <= len(self.blocks)
        index = sorted(random.sample(range(len(self.blocks)), size))
        ret = Buffer()
        ret.blocks = [self.blocks[i] for i in index]
        return ret

    def marry(self, dbuf, size, split_by_relevance=True, min_positive_sample=1):
        if not split_by_relevance:
            raise NotImplementedError
        else:
            pbuf = dbuf.filtered(lambda blk, idx: hasattr(blk, 'relevance'))
            nbuf = dbuf.filtered(lambda blk, idx: not hasattr(blk, 'relevance'))
            ret = []
            for i in range(size):
                pos_num = random.randint(min_positive_sample, len(pbuf)) # determine the number of positive blks at random
                tmp_buf = self.random_sample(pos_num)
                # fill the temporal buffer to the full
                neg_indices = random.shuffle(range(len(nbuf)))
                tmp_size = tmp_buf.calc_size()
                for neg_idx in neg_indices:
                    if tmp_size + len(nbuf[neg_idx]) > CAPACITY:
                        break
                    else:
                        tmp_size += len(nbuf[neg_idx])
                        tmp_buf.insert(nbuf[neg_idx])
                ret.append(tmp_buf)
            return ret


    def export(self, length=None, out=None, device='cuda'):
        if out is None:
            if length is None:
                total_length = self.calc_size()
            else:
                total_length = length * len(self.blocks)

            if total_length > CAPACITY:
                raise ValueError('export inputs larger than capacity')

            ids, att_masks, type_ids = torch.zeros(3, total_length, dtype=torch.long, device=device)
        else: # must be zeros and big enough
            ids, att_masks, type_ids = out
        t = 0
        for b in self.blocks:
            if length is None:
                w = t + len(b)
            else:
                w = t + length
            ids[t:w] = b.ids # id
            if b.blk_type == 1:
                type_ids[t:w] = 1 # sentence B
            att_masks[t:t + len(b)] = 1 # attention_mask
            t = w
        return ids, att_masks, type_ids

    def export_as_batch(self, length=BLOCK_SIZE):
        buf = self.export(length)
        return buf.view(3, -1, length)

    def export_relevance(self, length=None, out=None):
        if out is None:
            total_length = self.calc_size() if length is None else length * len(self.blocks)
            relevance = torch.zeros(total_length, dtype=torch.long)
        else:
            relevance = out
        t = 0
        for b in self.blocks:
            w = t + (len(b) if length is None else length)
            if hasattr(b, 'relevance'):
                relevance[t: w] = b.relevance
            t = w
        return relevance

    def export_start_end(self, length=None, out=None)
        if out is None:
            total_length = self.calc_size() if length is None else length * len(self.blocks)
            start, end = torch.zeros(2, total_length, dtype=torch.long)
        else:
            start, end = out
        t = 0
        for b in self.blocks:
            w = t + (len(b) if length is None else length)
            if hasattr(b, 'start'):
                start[t + b.start[0]] = b.start[1]
            if hasattr(b, 'end'):
                end[t + b.end[0]] = b.end[1]
            t = w
        return start, end

if __name__ == "__main__":
    tokenizer = BertTokenizer.from_pretrained('bert-base-cased')
    document = ['']