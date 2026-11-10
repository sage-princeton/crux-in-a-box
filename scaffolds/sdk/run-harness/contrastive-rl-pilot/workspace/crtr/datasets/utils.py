import numpy as np
import gin

def get_mapping():    
    return  {'o':0, 'y':1, 'r':2, 'b':3, 'g':4, 'w':5}

def tokenize(batch_text):
    batch_num = []
    letter_to_number = get_mapping()
    for elt in batch_text:
        batch_num.append(float(letter_to_number[elt]))

    return batch_num

def detokenize(batch_num):
    batch_text = []
    number_to_letter = {v: k for k, v in get_mapping().items()}
    
    for elt in batch_num:
        batch_text.append(number_to_letter[int(elt)])

    return batch_text

def tokenize_all(batch_text):
    letter_to_number = get_mapping()
    result = np.zeros((len(batch_text), 21, 54), dtype=np.float32)
    for i, traj in enumerate(batch_text):

        for j, elt in enumerate(traj):
            for k, char in enumerate(elt):
                result[i, j, k] = letter_to_number[char]
        
    return result
    
def tokenize_pair(batch_text):
    batch_num = []
    letter_to_number = get_mapping()
    for elt in batch_text:
        batch_num.append(([float(letter_to_number[char]) for char in elt[0]], [float(letter_to_number[char]) for char in elt[1]]))

    return batch_num

def tokenize_traj(traj_text):
    traj_num = []
    letter_to_number = get_mapping()
    for elt in traj_text:
        traj_num.append([letter_to_number[char] for char in elt])
    return traj_num

@gin.configurable  
class DataLoader():
    def __init__(self, dataset, batch_size, split):
        self.dataset = dataset
        self.batch_size = batch_size
        self.split = split

    def __iter__(self):
        return self


    def __next__(self):
        return self.dataset._get_batch(self.batch_size, split=self.split)
