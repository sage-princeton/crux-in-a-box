import sys
import os
from abc import ABC, abstractmethod


class AbsLogger(ABC):
    @abstractmethod
    def log_scalar(self, name, value):
        raise NotImplementedError

    @abstractmethod
    def log_property(self, name, value):
        raise NotImplementedError


class StdoutLogger(AbsLogger):
    """Logs to standard output."""

    def __init__(self, file=sys.stderr, output_dir=None):
        self.file = file
        self.output_dir = output_dir

    def log_scalar(self, name, step, value):
        """Logs a scalar to stdout."""
        # Format:
        #      1 | accuracy:                   0.789
        #   1234 | loss:                      12.345
        #   2137 | loss:                      1.0e-5
        if 0 < value < 1e-2:
            print(
                "{:>6} | {:64}{:>9.1e}".format(step, name + ":", value), file=self.file
            )
        else:
            print(
                "{:>6} | {:64}{:>9.3f}".format(step, name + ":", value), file=self.file
            )
        

    def log_figure(self, name, step, value):
        if self.output_dir is not None:
            if not os.path.exists(os.path.join(self.output_dir, str(step))):
                os.makedirs(os.path.join(self.output_dir, str(step)))
            value.savefig(os.path.join(self.output_dir, str(step), f"{name}.png"))

    def log_property(self, name, value):
        pass        

    def log_message(self, message):
        print(message, file=self.file)


class Loggers:
    def __init__(self):
        self.loggers = []

    def register_logger(self, logger: AbsLogger):
        self.loggers.append(logger)

    def log_scalar(self, name, step, value):
        for logger in self.loggers:
            logger.log_scalar(name, step, value)

    def log_property(self, name, value):
        for logger in self.loggers:
            logger.log_property(name, value)

    def log_parameters(self, parameters):
        for logger in self.loggers:
            logger.log_parameters(parameters)
    
    def log_image(self, name, step, value):
        for logger in self.loggers:
            logger.log_image(name, step, value)

    def log_figure(self, name, step, value):
        for logger in self.loggers:
            logger.log_figure(name, step, value) 
    
    def log_message(self, message):
        for logger in self.loggers:
            logger.log_message(message)
