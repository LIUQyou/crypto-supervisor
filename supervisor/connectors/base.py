import abc
import asyncio

class BaseConnector(abc.ABC):
    """
    Abstract base class for all exchange WebSocket connectors.
    Defines the interface that connectors must implement.
    """

    @abc.abstractmethod
    async def start(self):
        """
        Start the connector: establish connection, subscribe to channels,
        and begin streaming messages. This should run until stop() is called.
        """
        pass

    @abc.abstractmethod
    async def stop(self):
        """
        Signal the connector to stop streaming and clean up any resources.
        """
        pass

    async def run(self):
        """
        Helper to run the connector until completion.
        """
        await self.start()
