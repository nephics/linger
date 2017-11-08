from setuptools import setup

setup(name='linger',
      version='0.2.2',
      description='Message queue and pubsub service with HTTP API',
      author='Jacob Svensson',
      author_email='jacob@nephics.com',
      license="http://www.apache.org/licenses/LICENSE-2.0",
      url='https://github.com/nephics/linger',
      packages=['linger'],
      entry_points={
        'console_scripts': [
          'linger = linger:main'
        ],
      },
      install_requires=['tornado>=4.5.2'],
      classifiers=[
          'Development Status :: 4 - Beta',
          'Intended Audience :: Developers',
          'License :: OSI Approved :: Apache Software License',
          'Programming Language :: Python :: 3',
          'Topic :: System :: Distributed Computing',
          'Topic :: System :: Networking'
      ])
