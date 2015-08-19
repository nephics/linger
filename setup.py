from setuptools import setup

setup(name='linger',
      version='0.1.0',
      description='Message queue and pubsub service with HTTP API',
      author='Jacob Sondergaard',
      author_email='jacob@nephics.com',
      license="http://www.apache.org/licenses/LICENSE-2.0",
      url='https://bitbucket.org/nephics/linger',
      scripts=['linger-queue', 'linger-pubsub'],
      requires=['tornado(>=4.2.1)'],
      classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: Apache Software License',
        'Programming Language :: Python :: 3',
        'Topic :: System :: Distributed Computing',
        'Topic :: System :: Networking'
      ])
