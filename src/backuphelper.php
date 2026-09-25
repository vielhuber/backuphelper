<?php
declare(strict_types=1);

namespace vielhuber\backuphelper;

final class backuphelper
{
    private string $config;

    /**
     * Path of the yaml config that lists all jobs.
     */
    public function config(string $config): self
    {
        $this->config = $config;
        return $this;
    }

    /**
     * Run all jobs or only the named ones; a failing job is reported and does not stop the others.
     *
     * @throws BackupException for an invalid config or an unknown job name
     */
    public function run(string ...$names): bool
    {
        $config = (new ConfigReader())->file($this->config)->read();
        $unknown = array_diff($names, array_map(fn(Job $job): string => $job->name, $config->jobs));
        if ($unknown !== []) {
            throw BackupException::invalidConfig('unknown job ' . implode(', ', $unknown) . '.');
        }
        $runner = (new JobRunner())->ftpsh($config->ftpsh);
        $success = true;
        foreach ($config->jobs as $job) {
            if ($names !== [] && !in_array($job->name, $names, true)) {
                continue;
            }
            try {
                $runner->run($job);
            } catch (BackupException $exception) {
                echo date('Y-m-d H:i:s') . ' ❌ ' . $job->name . ': ' . $exception->getMessage() . PHP_EOL;
                $success = false;
            }
        }
        return $success;
    }
}
