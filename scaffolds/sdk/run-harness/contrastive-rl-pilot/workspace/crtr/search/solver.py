from queue import PriorityQueue
import random

import gin

class SolverNode:
    def __init__(self, state, parent, depth, child_num, path, done):
        self.state = state
        self.parent = parent
        self.depth = depth
        self.child_num = child_num
        self.path = path
        self.done = done
        self.children = []
        self.hash = state

    def add_child(self, child):
        self.children.append(child)

    def set_value(self, value):
        self.value = value

@gin.configurable
class BestFSSolver():
    def __init__(self,
                 metric,
                 shuffles,
                 network,
                 n_actions=None,
                 goal_builder_class=None,
                 max_tree_size=None,
                 max_tree_depth=None,
                 checkpoint_path=None,
                 value_estimator_class=None,
                 ):
        super().__init__()
        self.max_tree_size = max_tree_size
        self.max_tree_depth = max_tree_depth
        self.goal_builder_class = goal_builder_class
        self.goal_builder = self.goal_builder_class(shuffles=shuffles)
        self.value_estimator_class = value_estimator_class
        self.value_estimator = self.value_estimator_class(network, checkpoint_path=checkpoint_path, metric=metric)
        self.buffer = []
        self.n_actions = n_actions
        
    def construct_networks(self):
        self.value_estimator.construct_networks()

    def solve(self, input, solved_state):
        solved = False        
        root = SolverNode(input, None, 0, 0, [], False)
        nodes_queue = PriorityQueue()

        root_value = self.value_estimator.get_solved_distance(root.state, solved_state)
        root.set_value(root_value)
        nodes_queue.put((root_value, random.random(), root))
        solution = []
        tree_size = 1
        expanded_nodes = 0
        all_goals_created = 0
        tree_depth = 0
        total_path_between_goals = 0
        seen_hashed_states = {str(root.hash)}
        finished_cause = 'Not determined'

        while True:
            if nodes_queue.empty() or tree_size >= self.max_tree_size or solved:
                break

            curr_val, _, current_node = nodes_queue.get()
            self.buffer.append(curr_val)
            if len(self.buffer) >= 10:
                self.buffer = self.buffer[1:]

            expanded_nodes += 1

            if self.max_tree_depth == -1 or current_node.depth < self.max_tree_depth:
                goals, solving_subgoal = self.goal_builder.build_goals(current_node.state)

                if solving_subgoal is not None:
                    solving_state, path, done = solving_subgoal
                    new_node = SolverNode(solving_state, current_node, current_node.depth + 1, 0,
                               path, True)
                    solution.append(new_node)
                    solved = True
                    finished_cause = 'Finished cause solved'
                    tree_size += 1
                    expanded_nodes += 1
                    break


                all_goals_created += len(goals)

                created_new = 0

                to_add_list = []
                for child_num, goal_proposition in enumerate(goals):
                    current_goal_state, current_path, _ = goal_proposition
                    current_goal_state_hash = str(current_goal_state)

                    if current_goal_state_hash not in seen_hashed_states:
                        new_node = SolverNode(current_goal_state, current_node, current_node.depth + 1, child_num, current_path, False)
                        node_val = self.value_estimator.get_solved_distance(new_node.state, solved_state)
                    
                        to_add_list.append((node_val, random.random(), goal_proposition))

                to_add_list = sorted(to_add_list)

                if self.n_actions is not None:
                        to_add_list = to_add_list[:self.n_actions]
                    
                for child_num, (node_val, _, goal_proposition) in enumerate(to_add_list):
                    current_goal_state, current_path, _ = goal_proposition
                    current_goal_state_hash = str(current_goal_state)
                    total_path_between_goals += len(current_path)

                    if current_goal_state_hash not in seen_hashed_states:
                        created_new += 1
                        seen_hashed_states.add(current_goal_state_hash)
                        new_node = SolverNode(current_goal_state, current_node, current_node.depth + 1, child_num, current_path, False)
                        current_node.add_child(new_node)
                        tree_depth = max(tree_depth, new_node.depth)
                        new_node.set_value(node_val)
                        nodes_queue.put((node_val, random.random(), new_node))
                        tree_size += 1



        tree_metrics = {'nodes' : tree_size,
                        'expanded_nodes': expanded_nodes,
                        'unexpanded_nodes': tree_size - expanded_nodes,
                        'max_depth' : tree_depth,
                        'avg_n_goals': all_goals_created/expanded_nodes if expanded_nodes > 0 else 0,
                        'avg_dist_between_goals' : total_path_between_goals/all_goals_created
                        if all_goals_created > 0 else 0
                        }

        additional_info = {'finished_cause': finished_cause}

        if solved:
            node = solution[0]
            while node.parent is not None:
                solution.append(node.parent)
                node = node.parent

            trajectory_actions = []
            for inter_goal in solution:
                trajectory_actions = list(inter_goal.path) + trajectory_actions

            inter_goals = [node for node in reversed(solution)]

            return (inter_goals, tree_metrics, root, trajectory_actions, additional_info)
        else:
            return (None, tree_metrics, root, None, additional_info)